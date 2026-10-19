"""Is the QC keeper in sync with the app?  Deterministic checks, no model call.

python tests/keeper_sync.py          the offline checks, on a throwaway library: the keeper session is replaced by a recorder, and after every kind of change to
                                     the library (a sample added, edited, archived, restored, deleted; a kind changed; a frame added or its role changed; the admin's marks
                                     drawn; a scenario in words only; an Example frame; a miss example that later gets a right version) the next question the keeper
                                     receives, for QC and for planning alike, must carry the current library again, while an unchanged library must not resend it
python tests/keeper_sync.py --live   a read-only look at the real library (nothing is written, no model is called): what the admin keeps in the database, what the
                                     keeper would be told, what QC is served over HTTP and by the pipeline's own commands, and what the keeper was last told
"""
import json
import os
import re
import sqlite3
import subprocess
import sys
import tempfile
from datetime import datetime
from pathlib import Path

LIVE = "--live" in sys.argv
HERE = Path(__file__).resolve().parent.parent
if not LIVE:
    TMP = Path(tempfile.mkdtemp(prefix="loupe-sync-"))
    os.environ["LOUPE_DATA"] = str(TMP / "data")
    os.environ["ATP_HOME"] = str(TMP / "atp")
    os.environ["ATG_HOME"] = str(TMP / "atg")
sys.path.insert(0, str(HERE))
from loupe_app import core, media, misses, qc  # noqa: E402

OK, BAD = [], []


def check(name, cond, extra=""):
    (OK if cond else BAD).append(name)
    print(("  ok   " if cond else "  FAIL ") + name + (f"   {extra}" if extra and not cond else ""))


def finish():
    print(f"\n{len(OK)} passed, {len(BAD)} failed")
    if BAD:
        print("FAILED: " + "; ".join(BAD))
    sys.exit(1 if BAD else 0)


ROLE_WORD = {"wrong": "WRONG", "right": "RIGHT", "example": "EXAMPLE", "neutral": "EXAMPLE"}


def digest_lines() -> dict:
    """id -> the keeper's digest line."""
    return {l.split(" | ")[0]: l for l in qc._digest().splitlines() if re.match(r"S\d+ \|", l)}


def roles_in(line: str) -> list:
    return sorted(re.findall(r"\b(WRONG|RIGHT|EXAMPLE) \d+:\d\d\.\d\d", line))


def db_rows() -> list:
    """The active samples straight from the database, written independently of the modules under test: (moment id, miss status, moment status, frames, kind)."""
    con = core.db()
    try:
        return [dict(r) for r in con.execute("SELECT m.id AS id, m.frames AS frames, m.status AS mstatus, x.status AS xstatus, x.wrong_path AS wp, x.fixed_path AS fp, m.t_wrong AS tw, m.t_fixed AS tf "
                                             "FROM moments m JOIN misses x ON x.id = m.miss_id ORDER BY m.id")]
    finally:
        con.close()


def expected_roles(r) -> list:
    """The frames stored for a moment, as the digest must name them (a moment saved before frames existed holds the miss frame and, with a right version, the right frame)."""
    if r["frames"] not in (None, ""):
        return sorted(ROLE_WORD.get(f.get("role"), "WRONG") for f in json.loads(r["frames"]) if isinstance(f, dict) and "t" in f)
    return sorted((["WRONG"] if r["wp"] else []) + (["RIGHT"] if r["fp"] else []))


def same_library(where: str):
    """The admin's database, the library QC reads, the keeper's digest and the deterministic brief name the same samples, with the same frame tags and kind."""
    rows = db_rows()
    active = [r for r in rows if r["mstatus"] == "active" and r["xstatus"] == "active"]
    ids_db = {f"S{r['id']}" for r in active}
    lib = qc.library()
    ids_lib = {s["id"] for s in lib}
    dig = digest_lines()
    ids_brief = {s["id"] for s in qc.search({}, 10000)}
    check(f"{where}: the library QC reads is the active samples of the database", ids_db == ids_lib, f"db {sorted(ids_db)} lib {sorted(ids_lib)}")
    check(f"{where}: the keeper's digest has one line per sample and none for an archived or deleted one", ids_db == set(dig), f"db {sorted(ids_db)} digest {sorted(dig)}")
    check(f"{where}: the deterministic brief can reach every sample", ids_db == ids_brief)
    bad = [f"S{r['id']}" for r in active if f"S{r['id']}" in dig and roles_in(dig[f"S{r['id']}"]) != expected_roles(r)]
    check(f"{where}: every digest line names the frames stored for its sample (WRONG, RIGHT, EXAMPLE)", not bad, ", ".join(bad))
    kind_bad = []
    for s in lib:
        ln, roles = dig.get(s["id"], ""), set(roles_in(dig.get(s["id"], "")))
        want = "pair" if "WRONG" in roles and "RIGHT" in roles else ("miss-only" if "WRONG" in roles else "scenario")
        ok = s["sample_kind"] == want and (("NO RIGHT VERSION" in ln) == (want == "miss-only")) and (("SCENARIO" in ln) == (want == "scenario"))
        if not ok:
            kind_bad.append(f"{s['id']} {s['sample_kind']}/{want}")
    check(f"{where}: pair, miss example and scenario are told apart the same way in the library and in the digest", not kind_bad, ", ".join(kind_bad))


# ====================================================================================================== the real library, read only
def live():
    def ro():
        con = sqlite3.connect(f"file:{core.DATA / 'loupe.db'}?mode=ro", uri=True, timeout=30)
        con.row_factory = sqlite3.Row
        return con
    core.db = ro            # every read of the app's modules goes through a read-only door: a write would raise instead of changing the real library
    sys.path.insert(0, str(HERE))
    import loupe_client  # noqa: E402

    print(f"[the real library: {core.DATA}]")
    rows = db_rows()
    n_act = sum(1 for r in rows if r["mstatus"] == "active" and r["xstatus"] == "active")
    kinds = {}
    for s in qc.library():
        kinds[s["sample_kind"]] = kinds.get(s["sample_kind"], 0) + 1
    ex = sum(1 for s in qc.library() for f in s["frames"] if f["role"] == "example")
    print(f"  {len(rows)} samples in the database, {n_act} active: {kinds or 'none'}; {ex} Example frame(s)")
    same_library("database and keeper")

    lib = qc.library()
    missing = [s["id"] for s in lib if not os.path.isfile(s["composite_path"]) or os.path.getsize(s["composite_path"]) < 1000]
    check("every active sample has its image, the file the keeper is told to read", not missing, ", ".join(missing))
    stale = []
    for s in lib:
        try:
            age = abs(datetime.fromisoformat(s["updated"]).timestamp() - os.stat(s["composite_path"]).st_mtime)
        except (OSError, ValueError):
            continue
        if age > 120:
            stale.append(f"{s['id']} ({int(age)} s)")
    check("every image was rebuilt with its sample's last edit (no image older than its words)", not stale, ", ".join(stale))

    h = loupe_client.health()
    if not h:
        print("  skip: Loupe is not running, so what QC is served over HTTP and what the pipeline commands see were not checked")
    else:
        s, d = loupe_client.call("GET", "/api/qc/samples?limit=10000", key_name="qc", timeout=60)
        ids_http = {x["id"] for x in d.get("samples", [])} if s == 200 else set()
        ids_lib = {x["id"] for x in lib}
        check("QC is served the same library over HTTP (the running server and the database agree)", s == 200 and ids_http == ids_lib and d.get("library_size") == len(lib), f"http {s} {sorted(ids_http)} lib {sorted(ids_lib)}")
        kind_ok = all(x["sample_kind"] == next(y for y in lib if y["id"] == x["id"])["sample_kind"] and x["n_frames"] == len(next(y for y in lib if y["id"] == x["id"])["frames"]) for x in d.get("samples", []))
        check("and each sample arrives with its kind and its frame count (miss example, scenario, Example frames)", kind_ok)
        bad_img = []
        for x in lib:
            st, body = loupe_client.call("GET", f"/api/qc/samples/{x['id']}/image", key_name="qc", raw=True, timeout=60)
            if st != 200 or not isinstance(body, (bytes, bytearray)) or body[:4] != b"\x89PNG":
                bad_img.append(x["id"])
        check("QC can fetch every sample's image", not bad_img, ", ".join(bad_img))
        for name, script in (("analyze-then-generate", "atg.py"), ("analyze-then-pick", "atp.py")):
            p = Path.home() / ".claude" / name / "scripts" / script
            if not p.exists():
                continue
            cp = subprocess.run([sys.executable, "-X", "utf8", str(p), "qc-brief", "--question", "keeper sync check"], capture_output=True, text=True, encoding="utf-8", timeout=90)
            last = [ln for ln in cp.stdout.strip().splitlines() if ln.strip()]
            try:
                js = json.loads(last[-1]) if last else {}
            except ValueError:
                js = {}
            check(f"`{script} qc-brief` (the QC phase of that pipeline) sees the same library", js.get("library_size") == len(lib) and js.get("mode") == "brief", cp.stdout[-200:] + cp.stderr[-200:])

    print("\n[what the keeper was last told]")
    st = core.setting_get("keeper", {}) or {}
    sig = qc._sig()
    if not st.get("session_id"):
        print("  no keeper session has been started yet: the first question will boot it with the whole library")
    else:
        sync = st.get("library_sig") == sig
        print(f"  session {st['session_id'][:8]}…, {st.get('turns', 0)} turn(s), model {st.get('model')}, last used {st.get('last_used', '-')}")
        print("  " + ("IN SYNC: the keeper holds the library exactly as it is now." if sync else
                      "OUT OF DATE: the library changed since the keeper's last digest; the server's watcher (or the next run start) sends it the whole library; a question that arrives first resends it itself (nothing is lost)."))
        check("the keeper's remembered version is a version this library has had (not a different library)", bool(re.match(r"^\d+:[0-9a-f]{16}$", st.get("library_sig", ""))))
    finish()


if LIVE:
    live()

# ====================================================================================================== the throwaway library
core.ensure_dirs()


def mkvideo(path: Path, color: str):
    path.parent.mkdir(parents=True, exist_ok=True)
    cp = subprocess.run([media.find_tool("ffmpeg"), "-y", "-loglevel", "error", "-f", "lavfi", "-i", f"color=c={color}:s=360x640:r=30:d=3", "-pix_fmt", "yuv420p", str(path)], capture_output=True)
    assert cp.returncode == 0, cp.stderr[-300:]


run = TMP / "atp" / "runs" / "20261001-000000-07"
a1, a2 = run / "60-outputs" / "video-03" / "a1" / "raw.mp4", run / "60-outputs" / "video-03" / "a2" / "raw.mp4"
mkvideo(a1, "0x305090")
mkvideo(a2, "0x509030")

calls: list[str] = []


def fake_claude(prompt, sid, resume, model, timeout=150):         # the keeper, replaced by a recorder
    calls.append(prompt)
    return {"result": json.dumps({"relevant": [], "advice": "", "not_covered": ""}), "total_cost_usd": 0}


qc._run_claude = fake_claude
CTX = {"run": "20261001-000000-07", "video": "03", "model": "wan3_0_prime"}
DELTA = "The library changed since your last digest"


def ask():
    """One QC question; (did the keeper receive a fresh library, was it the current one, the prompt)."""
    n = len(calls)
    qc.consult("sync-test", dict(CTX), "the can looks different after the cut", "qc")
    new = calls[n:]
    text = "\n".join(new)
    return (DELTA in text or "LIBRARY (" in text), qc._digest() in text, text


def ask_plan():
    """One planning question (the first request of a video): the same keeper, the same library."""
    n = len(calls)
    qc.consult("sync-test", dict(CTX), "Beat 1: he sets the can on the desk.\nBeat 2: he lifts it.", "plan")
    text = "\n".join(calls[n:])
    return (DELTA in text or "LIBRARY (" in text), qc._digest() in text, text


def mk_miss(title, video="03", kinds=None):
    m = misses.create({"run": CTX["run"], "video": video, "title": title, "model": "wan3_0_prime", "kinds": kinds or ["continuity"], "wrong_path": str(a1), "fixed_path": str(a2)}, "test")
    mo = misses.add_moment(m["id"], {"frames": [{"src": "wrong", "role": "wrong", "t": 1.0}, {"src": "fixed", "role": "right", "t": 1.0}], "details": "the can is small here"}, "test")
    return m["id"], mo["moment_id"]


def mk_only(title, frames, video="05", **extra):
    """A report with Video A only (no right version) and one moment with the frames given."""
    m = misses.create({"run": CTX["run"], "video": video, "title": title, "model": "wan3_0_prime", "kinds": ["continuity"], "wrong_path": str(a1)}, "test")
    mo = misses.add_moment(m["id"], {"frames": frames, "details": "the admin's words about this moment", **extra}, "test")
    return m["id"], mo["moment_id"]


def line_of(moment_id) -> str:
    return digest_lines().get(f"S{moment_id}", "")


print("[the keeper and the library]")
check("an empty library starts no keeper session", not ask()[0] and not calls)
A, Am = mk_miss("The can shrinks after the cut")
fresh, current, _ = ask()
check("the first question boots the keeper with the whole current library", fresh and current)
fresh, current, _ = ask()
check("an unchanged library is not sent again", not fresh)

B, Bm = mk_miss("The label reads backwards")
fresh, current, _ = ask()
check("a new sample reaches the keeper", fresh and current)

misses.update(A, {"title": "The can shrinks drastically after the second cut"}, "test")
fresh, current, _ = ask()
check("an edited title reaches the keeper", fresh and current)

misses.update(A, {"kinds": ["scale-size", "continuity"]}, "test")
fresh, current, text = ask()
check("a kind changed on the report reaches the keeper (the digest lists kinds)", fresh and current)

misses.update_moment(Am, {"details": "the can is half its size at 0:01"}, "test")
fresh, current, _ = ask()
check("an edited explanation reaches the keeper", fresh and current)

misses.update_moment(Am, {"idea": "The can should keep its size from the first shot to the last", "missed": "The agents let the distance change the size"}, "test")
fresh, current, text = ask()
check("the idea and what the agents missed reach the keeper", fresh and current and "agents missed: The agents let the distance" in qc._digest(), qc._digest()[:300])

misses.update_moment(Am, {"frames": [{"src": "wrong", "role": "right", "t": 0.5}, {"src": "wrong", "role": "wrong", "t": 2.0}, {"src": "fixed", "role": "right", "t": 2.0}]}, "test")
fresh, current, text = ask()
check("a frame added to a sample reaches the keeper", fresh and current)
check("and the digest says which frames the image holds", "frames:" in qc._digest() and "3 frames" in qc._digest(), qc._digest()[:400])
check("and the keeper is told that an image it already looked at may have changed", "image" in text.split(DELTA)[-1].lower() and "again" in text.split(DELTA)[-1].lower() if DELTA in text else False, text[-400:])

misses.update(B, {"status": "archived"}, "test")
fresh, current, text = ask()
check("an archived report leaves the keeper's library", fresh and current and "label reads backwards" not in text.split(DELTA)[-1])

C, Cm = mk_miss("The pack turns into a different flavour", video="04")
misses.update(A, {"status": "archived"}, "test")
misses.update(B, {"status": "active"}, "test")
fresh, current, text = ask()
check("one report archived and another restored between two questions (the count stays the same) still reaches the keeper", fresh and current)

misses.delete_moment(Cm, "test")
fresh, current, _ = ask()
check("a deleted sample reaches the keeper", fresh and current)

misses.update(A, {"product": "a can, 12 oz", "mode": "analyze-recreate"}, "test")
fresh, current, _ = ask()
check("a product or mode change (not in the digest, used by the server's own ranking) does not make the keeper repeat itself", not fresh)
same_library("after the first round")

print("\n[scenarios in words, Example frames and miss examples]")
W = misses.create({"run": "", "video": "", "title": "The plan wanted the can to stay in hand", "model": "wan3_0_prime", "kinds": ["continuity"]}, "test")["id"]
Wm = misses.add_moment(W, {"frames": [], "idea": "The hand keeps the can until the last beat.", "better": "State the grip at every beat.", "missed": "The agents read the hold as optional.", "details": "Words only."}, "test")["moment_id"]
fresh, current, _ = ask()
check("a scenario in words only reaches the keeper", fresh and current)
ln = line_of(Wm)
check("and its digest line holds the idea, what could be better, what the agents missed, and says it has no frames",
      all(x in ln for x in ("SCENARIO", "idea: The hand keeps", "could be better: State the grip", "agents missed: The agents read", "no frames (the admin's words only)")), ln[:400])

O, Om = mk_only("The label reads as scribbles", [{"src": "wrong", "role": "wrong", "t": 1.0}])
fresh, current, _ = ask()
ln = line_of(Om)
check("a miss example (a wrong frame and no right version) reaches the keeper as a miss example", fresh and current and "NO RIGHT VERSION" in ln and roles_in(ln) == ["WRONG"], ln[:400])
misses.update(O, {"fixed_path": str(a2)}, "test")
fresh, current, _ = ask()
ln = line_of(Om)
check("a right version attached later reaches the keeper: the sample becomes a pair", fresh and current and "NO RIGHT VERSION" not in ln and roles_in(ln) == ["RIGHT", "WRONG"], ln[:400])
misses.update(O, {"fixed_path": ""}, "test")
fresh, current, _ = ask()
ln = line_of(Om)
check("and clearing it again makes it a miss example once more", fresh and current and "NO RIGHT VERSION" in ln and roles_in(ln) == ["WRONG"], ln[:400])

E, Em = mk_only("Keep this framing in mind", [{"src": "wrong", "role": "example", "t": 1.0}], idea="Look at how the can is held in this frame.")
fresh, current, _ = ask()
ln = line_of(Em)
check("a sample made only of an Example frame reaches the keeper, tagged EXAMPLE, as a scenario", fresh and current and roles_in(ln) == ["EXAMPLE"] and "SCENARIO" in ln and "NO RIGHT VERSION" not in ln, ln[:400])
misses.update_moment(Em, {"frames": [{"src": "wrong", "role": "wrong", "t": 1.0}]}, "test")
fresh, current, _ = ask()
ln = line_of(Em)
check("an Example frame changed to Wrong reaches the keeper (the role is what the keeper reads)", fresh and current and roles_in(ln) == ["WRONG"] and "SCENARIO" not in ln, ln[:400])
misses.update_moment(Em, {"frames": [{"src": "wrong", "role": "example", "t": 1.0}, {"src": "wrong", "role": "example", "t": 2.0}]}, "test")
fresh, current, _ = ask()
ln = line_of(Em)
check("and two Example frames are told as two", fresh and current and roles_in(ln) == ["EXAMPLE", "EXAMPLE"] and "2 frames" in ln, ln[:400])

before = qc._digest()
misses.update_moment(Em, {"shapes": [{"type": "rect", "frame": 0, "x1": .2, "y1": .3, "x2": .6, "y2": .5}]}, "test")
fresh, current, text = ask()
check("marks drawn on a frame (the words unchanged) still reach the keeper: its image was rebuilt", fresh and current and qc._digest() == before)

misses.update_moment(Wm, {"status": "archived"}, "test")
fresh, current, _ = ask()
check("one archived sample of a report leaves the keeper's library", fresh and current and not line_of(Wm))
misses.update_moment(Wm, {"status": "active"}, "test")
fresh, current, _ = ask()
check("and a restored one comes back", fresh and current and bool(line_of(Wm)))

print("\n[planning questions get the same library]")
fresh, current, _ = ask_plan()
check("an unchanged library is not resent to a planning question either", not fresh)
misses.update_moment(Wm, {"better": "State the grip at every beat, and again after every cut."}, "test")
fresh, current, _ = ask_plan()
check("a change reaches the keeper on a planning question as well", fresh and current)
fresh, current, _ = ask()
check("and the QC question after it does not resend it (one keeper, one memory)", not fresh)

print("\n[the keeper is kept current without a question (sync_keeper: run start, and the server's watcher)]")
n = len(calls)
r = qc.sync_keeper("test")
check("a current keeper is left alone: no model call", r["state"] == "in_sync" and len(calls) == n and qc.keeper_status()["in_sync"], str(r))
Sx, Sxm = mk_miss("The cap spins the wrong way")
check("a library change makes the keeper's status say out of date", not qc.keeper_status()["in_sync"])
n = len(calls)
r = qc.sync_keeper("test")
check("sync tells the keeper the changed library in one call, with the new sample in it", r["state"] == "synced" and len(calls) == n + 1 and "S%d |" % Sxm in calls[-1] and qc.keeper_status()["in_sync"], str(r))
fresh, current, _ = ask()
check("and the first question after the sync does not resend the library", not fresh)
core.setting_set("keeper", {**(core.setting_get("keeper", {}) or {}), "turns": qc.ROT_TURNS})
check("a worn-out keeper session shows as not current", not qc.keeper_status()["in_sync"])
n = len(calls)
r = qc.sync_keeper("test")
check("sync replaces a worn-out session with a fresh one holding the whole library", r["state"] == "booted" and len(calls) == n + 1 and qc._digest() in calls[-1] and qc.keeper_status()["in_sync"], str(r))
core.setting_set("keeper", {})
qc._run_claude = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("the claude CLI was not found on PATH"))
r = qc.sync_keeper("test")
check("a keeper that cannot start is reported as failed, never raised", r["state"] == "failed" and "claude" in r.get("error", ""), str(r))
qc._run_claude = fake_claude
r = qc.sync_keeper("test")
check("and the next sync boots it", r["state"] == "booted" and qc.keeper_status()["in_sync"], str(r))

print("\n[the same library everywhere]")
same_library("at the end")
finish()
