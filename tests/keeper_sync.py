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
    os.environ["AWL_HOME"] = str(TMP / "awl")
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
    """id -> that sample's record in the keeper's digest (from its === SAMPLE line to its === END OF SAMPLE line)."""
    return qc.records_of(qc._digest())


def roles_in(record: str) -> list:
    """The frames a sample's record lists in its own FRAMES lines ("  Frame 1 WRONG 0:01.00, Video A ..."), not the collector's words under READ, which
    may quote a frame's tag too."""
    return sorted(r for _n, r, _t in qc.record_frames(record))


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
        right_only = roles == {"RIGHT"}          # a scenario of frames called RIGHT only reads THE CORRECT VERSION ONLY (the admin, 2026-10-29)
        ok = (s["sample_kind"] == want and (("NO RIGHT VERSION" in ln) == (want == "miss-only"))
              and (("SCENARIO" in ln) == (want == "scenario" and not right_only)) and (("THE CORRECT VERSION ONLY" in ln) == right_only))
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
        sync = st.get("library_sig") == sig and st.get("header") == qc.HEADER_SIG
        print(f"  session {st['session_id'][:8]}…, {st.get('turns', 0)} turn(s), model {st.get('model')}, last used {st.get('last_used', '-')}")
        print("  " + ("IN SYNC: the keeper holds the library exactly as it is now." if sync else
                      "OUT OF DATE: the library, or the header that says how to read it, changed since the keeper's last digest; the server's watcher (or the next run start) sends it the whole library; a question that arrives first resends it itself (nothing is lost)."))
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


HELD: dict = {}


def absorb(prompt: str):
    """What the keeper holds after a message: a boot replaces everything with the lines of the library it carries; a delta (memory rot: only what changed is
    sent) replaces the lines it carries and drops the ids it names as removed."""
    global HELD
    if "LIBRARY (" in prompt:
        HELD = qc.records_of(prompt)
    elif DELTA in prompt:
        HELD.update(qc.records_of(prompt))
        m = re.search(r"Removed from the library, forget them: ([^\n]+)", prompt)
        for k in (m.group(1).split(",") if m else []):
            HELD.pop(k.strip(), None)


def _asked(n: int):
    new = calls[n:]
    for p in new:
        absorb(p)
    text = "\n".join(new)
    return (DELTA in text or "LIBRARY (" in text), HELD == digest_lines(), text


def ask():
    """One QC question; (did the keeper receive a fresh library or a delta, does it now hold the current library line for line, the prompt)."""
    n = len(calls)
    qc.consult("sync-test", dict(CTX), "the can looks different after the cut", "qc")
    return _asked(n)


def ask_plan():
    """One planning question (the first request of a video): the same keeper, the same library."""
    n = len(calls)
    qc.consult("sync-test", dict(CTX), "Beat 1: he sets the can on the desk.\nBeat 2: he lifts it.", "plan")
    return _asked(n)


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
check("the idea and what the agents missed reach the keeper", fresh and current and "the AI agents missed or could not understand: The agents let the distance" in qc._digest(), qc._digest()[:300])

misses.update_moment(Am, {"frames": [{"src": "wrong", "role": "right", "t": 0.5}, {"src": "wrong", "role": "wrong", "t": 2.0}, {"src": "fixed", "role": "right", "t": 2.0}]}, "test")
fresh, current, text = ask()
check("a frame added to a sample reaches the keeper", fresh and current)
check("and the digest says which frames the image holds", "FRAMES OF" in qc._digest() and "(3 frames)" in qc._digest(), qc._digest()[:400])
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
      all(x in ln for x in ("SCENARIO", "idea (what was meant to happen): The hand keeps", "could have been better: State the grip", "the AI agents missed or could not understand: The agents read", "no frames (the admin's words only)")), ln[:400])

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
check("marks drawn on a frame reach the keeper, and that frame's line in the sample's record names them", fresh and current and qc._digest() != before
      and "marks: 1 box" in line_of(Em), line_of(Em)[:600])
before = qc._digest()
misses.regenerate(Em)
fresh, current, text = ask()
check("an image rebuilt with the words and marks unchanged still reaches the keeper (its image is part of what it holds)", fresh and current and qc._digest() == before)

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
check("sync tells the keeper the changed library in one call, with the new sample in it", r["state"] == "synced" and len(calls) == n + 1 and "=== SAMPLE S%d " % Sxm in calls[-1] and qc.keeper_status()["in_sync"], str(r))
fresh, current, _ = ask()
check("and the first question after the sync does not resend the library", not fresh)
core.setting_set("keeper", {**(core.setting_get("keeper", {}) or {}), "turns": qc.ROT_TURNS})
check("a worn-out keeper session shows as not current", not qc.keeper_status()["in_sync"])
n = len(calls)
r = qc.sync_keeper("test")
check("sync replaces a worn-out session with a fresh one holding the whole library", r["state"] == "booted" and len(calls) == n + 1 and qc._digest() in calls[-1] and qc.keeper_status()["in_sync"], str(r))
# the library header (how a record is read) reaches a session only when it boots: a session told another header is replaced, as a model change is
core.setting_set("keeper", {**(core.setting_get("keeper", {}) or {}), "header": "an-older-header"})
check("a session told another library header shows as not current (a delta never sends the header again)", not qc.keeper_status()["in_sync"])
n = len(calls)
r = qc.sync_keeper("test")
check("sync replaces it with a fresh session told the current header and the whole library",
      r["state"] == "booted" and len(calls) == n + 1 and qc.LIB_HEADER in calls[-1] and qc._digest() in calls[-1] and qc.keeper_status()["in_sync"]
      and core.setting_get("keeper", {}).get("header") == qc.HEADER_SIG, str(r))
core.setting_set("keeper", {})
qc._run_claude = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("the claude CLI was not found on PATH"))
r = qc.sync_keeper("test")
check("a keeper that cannot start is reported as failed, never raised", r["state"] == "failed" and "claude" in r.get("error", ""), str(r))
qc._run_claude = fake_claude
r = qc.sync_keeper("test")
check("and the next sync boots it", r["state"] == "booted" and qc.keeper_status()["in_sync"], str(r))

print("\n[the keeper asks for what it cannot judge, and Loupe fetches it without polluting the library]")
import json as _json
import shutil as _shutil

RUNNAME = CTX["run"]
rd_ = TMP / "atp" / "runs" / RUNNAME
(rd_ / "40-briefs").mkdir(parents=True, exist_ok=True)
(rd_ / "50-requests").mkdir(parents=True, exist_ok=True)
(rd_ / "60-outputs" / "video-03" / "a1").mkdir(parents=True, exist_ok=True)
(rd_ / "30-assets").mkdir(parents=True, exist_ok=True)
(rd_ / "state.json").write_text("{}", encoding="utf-8")
(rd_ / "40-briefs" / "video-03-plan.md").write_text("PLAN-MARK checkpoint 0.5 s: the can stays on the desk", encoding="utf-8")
(rd_ / "30-assets" / "registry.md").write_text("REGISTRY-MARK slim can 15.7 cm", encoding="utf-8")
(rd_ / "50-requests" / "video-03-request.json").write_text(_json.dumps({"params": {"prompt": "REQUEST-MARK a can on the desk"}}), encoding="utf-8")
mkvideo(rd_ / "60-outputs" / "video-03" / "a1" / "raw.mp4", "blue")

script = []


def scripted(prompt, sid, resume, model, timeout=150):
    calls.append(prompt)
    out = script.pop(0) if script else {"relevant": [], "advice": "", "not_covered": ""}
    return {"result": _json.dumps(out), "total_cost_usd": 0}


qc._run_claude = scripted
ask()                                  # the library is current from here on, so a consult is one keeper turn plus the asks
turns0 = core.setting_get("keeper")["turns"]
n = len(calls)
script[:] = [{"relevant": [], "needs": [{"kind": "video_plan", "why": "what the plan intended"}, {"kind": "video_frame", "t": 0.5, "why": "the can at 0.5 s"},
                                        {"kind": "ask_agent", "question": "is the tab up at 4 s?", "why": "cannot see it"}]},
             {"relevant": [{"id": qc.library()[0]["id"], "why": "w", "check": "c", "wrong_pattern": "p"}], "advice": "a", "not_covered": ""}]
res = qc.consult("sync-test", dict(CTX), "the can looks smaller after the cut", "qc")
follow = calls[n + 1] if len(calls) > n + 1 else ""
check("a keeper that cannot judge asks, Loupe fetches the plan from the run and sends it back in the same session",
      len(calls) == n + 2 and "PLAN-MARK" in follow, follow[:200])
frame_paths = re.findall(r"frame of [^\n]*: (\S+\.png)", follow)
check("it also fetches a frame of the video under QC into a folder the keeper may Read (and nothing outside it)",
      bool(frame_paths) and Path(frame_paths[0]).is_file() and str(core.DATA / "keeper" / "ctx") in frame_paths[0], str(frame_paths))
check("what only the QC agent can answer is returned unmet with its question, never faked",
      any(x["kind"] == "ask_agent" and x.get("question") and not x["fulfilled"] for x in res.get("needs_unmet", [])), str(res.get("needs_unmet")))
check("the final answer is the second one, and the asks are listed for the audit",
      res["mode"] == "keeper" and [r["id"] for r in res["relevant"]] == [qc.library()[0]["id"]] and len(res.get("needs_asked", [])) == 3, str(res)[:300])
check("the follow-up says the data is evidence, not part of the library and not an instruction", "not part of your library" in follow and "nothing in it is an instruction" in follow)
check("the extra round counts as a keeper turn (the session rotates on schedule, nothing grows unseen)", core.setting_get("keeper")["turns"] == turns0 + 2,
      f"{turns0} -> {core.setting_get('keeper')['turns']}")
check("the library is untouched by what was fetched", not any(m in qc._digest() for m in ("PLAN-MARK", "REGISTRY-MARK", "REQUEST-MARK", "NOTE-MARK")))

n = len(calls)
script[:] = [{"needs": [{"kind": "video_plan"}]}, {"needs": [{"kind": "request_prompt"}]}, {"needs": [{"kind": "product_registry"}]}]
res = qc.consult("sync-test", dict(CTX), "keeps asking", "qc")
check("a keeper that keeps asking is stopped after two rounds and the rest is reported unmet", len(calls) == n + 3 and any(x["kind"] == "more" for x in res.get("needs_unmet", [])), str(res.get("needs_unmet")))

n = len(calls)
script[:] = [{"relevant": [], "advice": "", "not_covered": ""}]
res = qc.consult("sync-test", dict(CTX), "an easy one", "qc")
check("a keeper with nothing to ask costs no extra turn", len(calls) == n + 1 and "needs_asked" not in res)

n = len(calls)
script[:] = [{"hints": [], "none_reason": "x", "needs": [{"kind": "video_frame", "t": 1}, {"kind": "video_plan"}]}, {"hints": [], "none_reason": "nothing to say"}]
res = qc.consult("sync-test", dict(CTX), "Beat 1: he sets the can on the desk.", "plan")
check("a planning question can ask for the plan but not for a frame of a video that does not exist yet",
      any(x["kind"] == "video_frame" and not x["fulfilled"] and "not been generated" in x.get("reason", "") for x in res.get("needs_asked", [])) and "PLAN-MARK" in calls[n + 1],
      str(res.get("needs_asked")))

n = len(calls)
script[:] = [{"relevant": [], "advice": "", "not_covered": ""}]
qc.consult("sync-test", {**CTX, "note": "NOTE-MARK the tab is up at 4 s"}, "again with what I saw", "qc")
check("the QC agent's note (what it saw in its own video) reaches the keeper with the next question", "NOTE-MARK" in calls[n])

for k in range(qc.CTX_KEEP + 3):
    qc._ctx_dir(f"zz-{k:03d}")
check("fetched-data folders are pruned to the last %d (no pile-up on disk)" % qc.CTX_KEEP, len(list((core.DATA / "keeper" / "ctx").iterdir())) <= qc.CTX_KEEP)
_shutil.rmtree(core.DATA / "keeper" / "ctx", ignore_errors=True)

recs = digest_lines()
check("a sample's record says where the sample came from (run, video, time, mode)", all(f"WHERE {s_['id']} CAME FROM: " in recs.get(s_["id"], "") and "from run" in recs.get(s_["id"], "")
      for s_ in qc.library() if s_.get("run")), str(list(recs.values())[:1])[:300])
check("a sample's record has no empty 'wrong here: / note: / correct:' line", not any(re.search(r"^  S\d+ · (wrong here|note|correct)[^\n:]*: *$", r, re.M) for r in recs.values()))
check("the boot message explains every part of a record and that an empty kinds field means untagged", "One record per sample" in qc.LIB_HEADER
      and "empty kinds field" in qc.LIB_HEADER and "WHERE S.. CAME FROM" in qc.LIB_HEADER and "FRAMES OF S.." in qc.LIB_HEADER and "PROMPTS OF S.." in qc.LIB_HEADER)
qc._run_claude = fake_claude

print("\n[the prompts of the two boxes: in the record, read whole by the keeper on request, never in its digest, never diffed]")
qc._run_claude = fake_claude
pm_id, pm_mo = mk_miss("the can is set down and shrinks", video="08")
qc.keeper_reset("test")                # the hang test above booted a session through another recorder: start from one this block saw whole
ask()                                  # the new sample reaches the keeper; what follows must come from the prompts alone
misses.update(pm_id, {"wrong_prompt": "WRONGPROMPT-MARK the can on the desk.", "fixed_prompt": "FIXEDPROMPT-MARK the can on the desk, as tall as the mug.", "prompt_note": "NOTE-ON-PROMPT the height was stated"}, "test")
fresh, current, text = ask()
check("saving the prompts of both boxes re-syncs the keeper (the library version counts them)", fresh and current)
line = line_of(pm_mo)
check("its digest line says which prompts are saved, named by what they are, and carries the admin's note, never the prompts",
      "PROMPTS SAVED: FLAWED + CORRECT; sample_origin reads them" in line
      and "NOTE-ON-PROMPT" in line and "WRONGPROMPT-MARK" not in line and "FIXEDPROMPT-MARK" not in line, line[:300])
check("the header and the role tell the keeper to read the prompts through sample_origin and judge what changed itself, never to assume", "sample_origin" in qc.LIB_HEADER and "Never assume what changed without reading them" in qc.ROLE)
qc._run_claude = scripted
script[:] = [{"relevant": [], "needs": [{"kind": "sample_origin", "id": f"S{pm_mo}", "why": "what the correct version's prompt said"}]},
             {"relevant": [{"id": f"S{pm_mo}", "why": "w", "check": "c", "wrong_pattern": "p"}], "advice": "", "not_covered": ""}]
res = qc.consult("sync-test", dict(CTX), "how was the height fixed", "qc")
check("sample_origin brings both prompts whole to the keeper, as evidence for that answer, and nothing of them enters the library", "WRONGPROMPT-MARK" in calls[-1] and "FIXEDPROMPT-MARK" in calls[-1] and "NOTE-ON-PROMPT" in calls[-1] and "WRONGPROMPT-MARK" not in qc._digest(), calls[-1][:400])
check("the answer says the sample has a prompt pair and the admin's note, and never the prompts or a diff", res["relevant"][0]["prompts"] == "pair" and res["relevant"][0]["prompt_note"].startswith("NOTE-ON-PROMPT") and not any(k in res["relevant"][0] for k in ("wrong_prompt", "fixed_prompt", "prompt_change", "diff")), str(sorted(res["relevant"][0]))[:300])
script[:] = [{"hints": [{"id": f"S{pm_mo}", "trigger": "the can is set down after a cut", "right_outcome": "the can keeps its height", "lever": "state the height against the mug at that beat", "caveat": "", "confidence": "high"}]}]
res = qc.consult("sync-test", dict(CTX), "Beat 1: the can is set down.", "plan")
check("a planning hint carries the same marker and note, nothing more", res["hints"][0]["prompts"] == "pair" and res["hints"][0]["prompt_note"].startswith("NOTE-ON-PROMPT") and "wrong_prompt" not in res["hints"][0], str(res["hints"])[:300])
misses.delete(pm_id, "test")
qc._run_claude = fake_claude
ask()

print("\n[the consult answer carries the admin's words, honest reasons, fields cut at a sentence]")
qc._run_claude = scripted
n = len(calls)
sid0 = qc.library()[0]["id"]
script[:] = [{"relevant": [{"id": sid0, "why": "w. " * 300, "check": "c", "wrong_pattern": "p"}], "advice": "a", "not_covered": ""}]
res = qc.consult("sync-test", dict(CTX), "the words", "qc")
r0 = res["relevant"][0]
check("a relevant sample carries its title, model, run, video, time and kinds (on the real library the admin's words are in the title)",
      all(k in r0 for k in ("title", "model", "run", "video", "t_wrong", "kinds")) and r0["title"] == qc.library()[0]["title"], str(sorted(r0))[:200])
check("a long keeper field is cut at a sentence end and says so", r0["why"].endswith(". …") and len(r0["why"]) <= 603, r0["why"][-30:])
check("_clip keeps a short text whole and cuts a long one at a sentence end", qc._clip("short text", 50) == "short text" and qc._clip("a" * 40 + ". " + "b" * 100, 80) == "a" * 40 + ". …")
script[:] = [{"relevant": [], "needs": [{"kind": "video_frame", "t": 0.5, "why": "the can"}]}, {"relevant": [], "advice": "", "not_covered": ""}]
res = qc.consult("sync-test", {**CTX, "run": "20269999-000000-99"}, "a run Loupe does not hold", "qc")
check("a run that Loupe cannot find is named as such in the fetch reason (not 'no run named')",
      any("not under Loupe's run folders" in x.get("reason", "") for x in res.get("needs_asked", [])), str(res.get("needs_asked"))[:200])
script[:] = [{"needs": [{"kind": "video_plan"}]}, {"needs": [{"kind": "request_prompt"}]}, {"needs": [{"kind": "video_frame", "t": 2.5, "why": "the cut"}]}]
res = qc.consult("sync-test", dict(CTX), "keeps asking", "qc")
check("needs_unmet lists only what is unmet, and 'more' names the kinds and times still wanted, not a cut JSON string",
      all(x.get("fulfilled") is False or x["kind"] == "more" for x in res["needs_unmet"]) and any(x["kind"] == "more" and "video_frame at 2.5 s" in x["why"] for x in res["needs_unmet"]), str(res.get("needs_unmet"))[:300])
script[:] = [{"hints": [{"id": sid0, "trigger": "never let the can move", "right_outcome": "x", "lever": "y", "caveat": "", "confidence": "high", "model_independent": False},
                        {"id": "S999", "trigger": "t", "right_outcome": "x", "lever": "y", "caveat": "", "confidence": "high", "model_independent": True},
                        {"id": sid0, "trigger": "Beat 2: the can stands on the desk after the cut. " * 20, "right_outcome": "r", "lever": "l",
                         "caveat": "Does not apply when a hand lifts it. " * 20, "confidence": "medium", "model_independent": False}], "none_reason": ""}]
res = qc.consult("sync-test", dict(CTX), "Beat 1: the can on the desk.\nBeat 2: a cut.", "plan")
check("held_back_why says why each hint was held back (a universal, an unknown id)",
      res.get("held_back") == 2 and {w["reason"][:12] for w in res.get("held_back_why", [])} == {"worded as a ", "not a sample"}, str(res.get("held_back_why")))
h0 = res["hints"][0] if res.get("hints") else {}
check("a hint's trigger and caveat are cut at a sentence end, never mid-word", h0.get("trigger", "").endswith(". …") and h0.get("caveat", "").endswith(". …") and len(h0.get("trigger", "")) <= 603, str(h0)[:200])

print("\n[a keeper call that hangs does not hold every later consult]")
import threading as _threading  # noqa: E402
import time as _time  # noqa: E402
qc.LOCK_WAIT, qc.SYNC_LOCK_WAIT = 1, 1
qc._keeper_lock.acquire(timeout=1)          # as if a keeper call were running in another thread
t0 = _time.time()
res = qc.consult("sync-test", dict(CTX), "while the keeper is busy", "qc")
el = _time.time() - t0
check("a QC consult while another keeper call runs answers from the brief within the lock wait, mode busy", res["mode"] == "busy" and el < 6 and "samples" in res, f"{res['mode']} {el:.1f}s")
res = qc.consult("sync-test", dict(CTX), "Beat 1: x.", "plan")
check("a planning consult while busy gives no hints and says busy", res["mode"] == "busy" and res["hints"] == [] and res.get("keeper_error", "").startswith("busy"))
check("a sync while busy reports busy and costs nothing", qc.sync_keeper("test")["state"] == "busy")
qc._keeper_lock.release()
qc.LOCK_WAIT, qc.SYNC_LOCK_WAIT = 120, 20
n = len(calls)
script[:] = [{"relevant": [], "advice": "", "not_covered": ""}]
res = qc.consult("sync-test", dict(CTX), "after the lock is free", "qc")
check("once the lock is free the keeper answers again", res["mode"] == "keeper" and len(calls) == n + 1)
import importlib as _importlib  # noqa: E402
wrapper = TMP / "slow-claude.cmd"
wrapper.write_text('@echo off\r\n"%s" -c "import time; time.sleep(30)"\r\n' % sys.executable, encoding="utf-8")
_saved = sys.modules.pop("loupe_app.qc")
qc2 = _importlib.import_module("loupe_app.qc")
sys.modules["loupe_app.qc"] = _saved
qc2._claude = lambda: str(wrapper)
qc2.ROLE = "role"          # the wrapper is a .cmd: keep pipes and quotes out of its arguments (the real claude.cmd gets the full role)
t0 = _time.time()
try:
    qc2._run_claude("x", "00000000-0000-0000-0000-000000000000", False, "m", timeout=2)
    msg = "no error"
except RuntimeError as e:
    msg = str(e)
el = _time.time() - t0
check("a keeper subprocess that hangs is ended with its process tree after the timeout and reported", "timed out" in msg and el < 12, f"{msg[:80]} {el:.1f}s")

print("\n[a keeper session that is gone is replaced by a fresh one, and the same call is answered (the admin, 2026-11-02)]")
lost_py = TMP / "lost-claude.py"
lost_py.write_text("import sys\nsys.stdin.read()\nsys.stderr.write('No conversation found with session ID: 11111111-2222-3333-4444-555555555555\\n')\nsys.exit(1)\n", encoding="utf-8")
lost_cmd = TMP / "lost-claude.cmd"
lost_cmd.write_text('@echo off\r\n"%s" "%s"\r\n' % (sys.executable, lost_py), encoding="utf-8")
qc2._claude = lambda: str(lost_cmd)
kinds_seen = []
for resume_ in (True, False):
    try:
        qc2._run_claude("x", "11111111-2222-3333-4444-555555555555", resume_, "m", timeout=30)
        kinds_seen.append("no error")
    except qc2.SessionLost as e:
        kinds_seen.append("lost: " + str(e)[:60])
    except RuntimeError as e:
        kinds_seen.append("failed: " + str(e)[:60])
check("the claude CLI's 'No conversation found' on a resume is SessionLost (a fresh session is due); the same words for a new session stay a failure",
      kinds_seen[0].startswith("lost: keeper session failed: No conversation found") and kinds_seen[1].startswith("failed: keeper session failed: No conversation"), str(kinds_seen))
gone: dict = {"sid": None}
lost_calls: list = []


def lost_cli(prompt, sid, resume, model, timeout=150):
    """The keeper as the claude CLI answers once the saved conversation of one session was removed: SessionLost for that id, normal answers otherwise."""
    lost_calls.append((sid, resume, prompt))
    if resume and sid == gone["sid"]:
        raise qc.SessionLost("keeper session failed: No conversation found with session ID: " + sid)
    if prompt.startswith("LIBRARY ("):
        return {"result": "ready", "total_cost_usd": 0}
    return {"result": json.dumps({"relevant": [], "advice": "", "not_covered": ""}), "total_cost_usd": 0}


qc._run_claude = lost_cli
qc.keeper_reset("test")
qc.sync_keeper("test")                         # a session the keeper holds, current with the library ...
held = core.setting_get("keeper")["session_id"]
gone["sid"] = held                             # ... whose saved conversation is then removed
st_ = core.setting_get("keeper")
st_["library_sig"] = "an older version"        # and the library changed since its digest: the question would carry a delta
core.setting_set("keeper", st_)
lost_calls.clear()
res = qc.consult("sync-test", dict(CTX), "Which samples apply now that the session is gone?", "qc")
new_sid = core.setting_get("keeper")["session_id"]
check("a consult whose session is gone boots a fresh one told the whole library, asks the same question there and answers as the keeper, saying so",
      res["mode"] == "keeper" and new_sid != held and "was not found by the claude CLI" in res.get("keeper_restarted", "")
      and [(c[0] == held, c[1]) for c in lost_calls] == [(True, True), (False, False), (False, True)] and lost_calls[2][0] == new_sid
      and lost_calls[1][2].startswith("LIBRARY (") and "Which samples apply now" in lost_calls[2][2], str([(c[0][:8], c[1], c[2][:30]) for c in lost_calls]))
check("the question to the fresh session carries no delta (it was just told the whole library); the one to the lost session did",
      DELTA in lost_calls[0][2] and DELTA not in lost_calls[2][2])
con_ = core.db()
try:
    aud = con_.execute("SELECT actor, target, detail FROM audit WHERE action='keeper.session_lost' ORDER BY id DESC LIMIT 1").fetchone()
finally:
    con_.close()
check("the lost session is written in the audit log, with the session it names", bool(aud) and aud["target"] == held and "fresh session" in aud["detail"], str(aud and dict(aud)))
gone["sid"] = new_sid
st_ = core.setting_get("keeper")
st_["library_sig"] = "an older version"        # the library changed again: a sync sends a delta to the session it holds, which is gone too
core.setting_set("keeper", st_)
lost_calls.clear()
r_ = qc.sync_keeper("test")
check("a sync whose session is gone boots a fresh one in its place and says so", r_["state"] == "booted" and "was not found" in r_.get("keeper_restarted", "")
      and core.setting_get("keeper")["session_id"] not in (held, new_sid) and [c[1] for c in lost_calls] == [True, False], str(r_))
check("a session that is current again needs nothing: the next sync is in sync without a call", qc.sync_keeper("test")["state"] == "in_sync")


def always_lost(prompt, sid, resume, model, timeout=150):
    lost_calls.append((sid, resume, prompt))
    if resume:
        raise qc.SessionLost("keeper session failed: No conversation found with session ID: " + sid)
    return {"result": "ready", "total_cost_usd": 0}


qc._run_claude = always_lost
qc.keeper_reset("test")
lost_calls.clear()
res = qc.consult("sync-test", dict(CTX), "A question to a session that is lost as soon as it starts.", "qc")
check("a session the consult has just started that reports lost is never booted again: one boot, then the brief's answer with the error",
      res["mode"] == "fallback" and "No conversation found" in res.get("keeper_error", "") and [c[1] for c in lost_calls] == [False, True]
      and "keeper_restarted" not in res, str([c[1] for c in lost_calls]) + " " + str(res.get("keeper_error"))[:80])
qc._run_claude = fake_claude
qc.keeper_reset("test")

print("\n[the composite says what its tags mean and skips a zoom that would repeat the panel]")
from PIL import Image as _Image  # noqa: E402
from loupe_app import compose as _compose  # noqa: E402
frs = [{"img": _Image.new("RGB", (360, 640), (90, 90, 90)), "role": "wrong", "desc": "as generated", "t": 1.0, "shapes": [{"type": "rect", "frame": 0, "x1": 0.0, "y1": 0.1, "x2": 0.9, "y2": 1.0}]},
       {"img": _Image.new("RGB", (360, 640), (120, 120, 120)), "role": "right", "desc": "correct version", "t": 1.2, "shapes": []}]
c1, z1, _a = _compose.build(frs, {"title": "t"}, "d", "")
frs[0]["shapes"] = [{"type": "rect", "frame": 0, "x1": 0.4, "y1": 0.4, "x2": 0.6, "y2": 0.7}]
c2, z2, _a = _compose.build(frs, {"title": "t"}, "d", "")
check("marks that cover most of the frame give no zoom (it would repeat the panel); small marks do", z1 is None and z2 is not None and c1.height < c2.height, f"{c1.height} {c2.height}")
frs = [{"img": _Image.new("RGB", (360, 640), (90, 90, 90)), "role": "wrong", "desc": "as generated", "t": 1.0, "shapes": [{"type": "text", "frame": 0, "x": 0.4, "y": 0.4, "text": "here"}]},
       {"img": _Image.new("RGB", (360, 640), (120, 120, 120)), "role": "right", "desc": "correct version", "t": 1.2, "shapes": [{"type": "circle", "frame": 1, "cx": 0.5, "cy": 0.5, "rx": 0.1, "ry": 0.1}]}]
c3, z3, _a = _compose.build(frs, {"title": "t"}, "d", "")
check("a zoom only under a frame marked with a circle, arrow or box: none for a text label alone, none cut around another frame's marks",
      z3 is not None and z3.getpixel((300, 260)) == _compose.PAPER and z3.getpixel((888, 260)) != _compose.PAPER, f"{z3 and z3.getpixel((300, 260))} {z3 and z3.getpixel((888, 260))}")
frs[0]["shapes"] = [{"type": "rect", "frame": 0, "x1": 0.0, "y1": 0.0, "x2": 1.0, "y2": 0.9}]
c4, z4, _a = _compose.build(frs, {"title": "t"}, "d", "")
check("a frame whose own marks cover most of it has no zoom, while another frame's small mark keeps its own",
      z4 is not None and z4.getpixel((300, 260)) == _compose.PAPER and z4.getpixel((888, 260)) != _compose.PAPER, f"{z4 and z4.getpixel((300, 260))} {z4 and z4.getpixel((888, 260))}")
frs[0]["desc"], frs[1]["desc"] = "correct version · run 20261019-235823-19 video 03", "original video"
c5, _z5, _a = _compose.build(frs, {"title": "t"}, "d", "")
check("a long source name shrinks its tag to fit its own panel, never over the next one", c5.getpixel((589, 55)) == _compose.PAPER and c5.getpixel((30, 55)) == _compose.RED,
      f"{c5.getpixel((589, 55))} {c5.getpixel((30, 55))}")
check("the tags' legend reads as before for the two videos, and names the original video and another video or run",
      _compose.sources_legend(["as generated", "correct version"]) == "Frames tagged 'as generated' are from video A (the version as generated); frames tagged 'correct version' are from video B (the right version, a different video)."
      and "the original video (the source clip the generation was made from, not generated)" in _compose.sources_legend(["as generated", "original video"])
      and "taken from video 04 of the same run" in _compose.sources_legend(["correct version · video 04"]) and "taken from run R video 03" in _compose.sources_legend(["as generated · run R video 03"]))

print("\n[the marks are drawn on the zoom only: a frame with a zoom stays clean above it, a frame without one keeps its marks]")
from PIL import ImageChops as _Chops  # noqa: E402
P1, P2 = (24, 74, 584, 1070), (608, 74, 1168, 1070)        # the two places of a row of two 360x640 frames (each panel 560 x 996)


def _plain(im, box, src):
    """The picture at box is exactly the frame as resized for its panel: nothing is drawn on it."""
    crop = im.crop(box)
    return _Chops.difference(crop, src.convert("RGB").resize(crop.size, _Image.LANCZOS)).getbbox() is None


def _has(im, box, test):
    return any(test(p) for p in im.crop(box).getdata())


_red = lambda p: p[0] > 200 and p[1] < 100 and p[2] < 100          # noqa: E731
_green = lambda p: p[1] > 140 and p[0] < 70 and p[2] < 110         # noqa: E731
g1, g2 = _Image.new("RGB", (360, 640), (90, 90, 90)), _Image.new("RGB", (360, 640), (120, 120, 120))
frs = [{"img": g1, "role": "wrong", "desc": "as generated", "t": 1.0, "shapes": [{"type": "rect", "frame": 0, "x1": 0.4, "y1": 0.4, "x2": 0.6, "y2": 0.7}]},
       {"img": g2, "role": "right", "desc": "as generated", "t": 1.2, "shapes": [{"type": "circle", "frame": 1, "cx": 0.5, "cy": 0.5, "rx": 0.1, "ry": 0.1}]}]
c6, z6, a6 = _compose.build(frs, {"title": "t"}, "d", "")
check("a frame with a zoom stays clean above it: both panels are the plain frames, each zoom (in the composite and the zoom file) carries its own frame's marks",
      _plain(c6, P1, g1) and _plain(c6, P2, g2) and z6 is not None and _has(z6, (24, 0, 584, 520), _red) and _has(z6, (608, 0, 1168, 520), _green)
      and _has(c6, (24, 1094, 584, 1614), _red) and _has(c6, (608, 1094, 1168, 1614), _green))
check("the full-size marked files keep every frame's marks in place", not _plain(a6[0], (0, 0, 360, 640), g1) and not _plain(a6[1], (0, 0, 360, 640), g2))
frs[0]["shapes"] = [{"type": "text", "frame": 0, "x": 0.4, "y": 0.4, "text": "here"}]              # a text label alone: no zoom
frs[1]["shapes"] = [{"type": "rect", "frame": 1, "x1": 0.0, "y1": 0.0, "x2": 1.0, "y2": 0.95}]      # marks over most of the frame: no zoom
c7, z7, _a = _compose.build(frs, {"title": "t"}, "d", "")
check("a frame without a zoom keeps its marks on its panel (a text label alone, marks over most of it), so no mark is lost",
      z7 is None and not _plain(c7, P1, g1) and _has(c7, P2, _green))
frs[0]["shapes"], frs[1]["shapes"] = [{"type": "rect", "frame": 0, "x1": 0.4, "y1": 0.4, "x2": 0.6, "y2": 0.7}], []
c8, z8, _a = _compose.build(frs, {"title": "t"}, "d", "", include_zoom=False)
check("with the zoom switched off the marks stay on the panel", z8 is None and _has(c8, P1, _red) and _plain(c8, P2, g2))
c9, z9, _a = _compose.build(frs[:1], {"title": "t"}, "d", "")
check("a frame alone in its row: the normal-range panel clean, its marks on the close range beside it only", z9 is None and _plain(c9, P1, g1) and _has(c9, P2, _red))
qc._run_claude = fake_claude

print("\n[memory rot: the keeper's context stays small and current]")
qc.keeper_reset("test")
ask()                                                       # a fresh session told the whole library once
n_lib = len(digest_lines())
r_id, r_mo = mk_miss("one more can that grows", video="09")
fresh, current, text = ask()
check("a one-sample change sends only that sample's record, never the whole library again", fresh and current and DELTA in text and "LIBRARY (" not in text
      and list(qc.records_of(text)) == [f"S{r_mo}"], text[:300])
st = core.setting_get("keeper", {})
st["chars"] = qc.ROT_CHARS + 1
core.setting_set("keeper", st)
fresh, current, text = ask()
check("a session that has carried more than ROT_CHARS characters is replaced by a fresh one told the whole library", fresh and current and "LIBRARY (" in text
      and core.setting_get("keeper", {}).get("session_id") != st["session_id"])
check("the keeper's bookkeeping of the lines it holds is not shown to a page or a pipeline", "lines" not in qc.keeper_status())
check("every Read the keeper makes counts toward the session's size, not only the text sent and received",
      qc._carried("abc", {"result": "xy", "num_turns": 3}) == 5 + 2 * qc.READ_CHARS and qc._carried("abc", {"result": "xy"}) == 5)
check("the keeper's thinking counts toward the session's size too (a resumed session keeps it, and a higher effort thinks longer)",
      qc._carried("abc", {"result": "xy", "usage": {"output_tokens_details": {"thinking_tokens": 100}}}) == 5 + 100 * qc.THINK_CHARS
      and qc._carried("abc", {"result": "xy", "modelUsage": {"m": {"thinkingTokens": 7}}}) == 5 + 7 * qc.THINK_CHARS)
s_any = qc.library()[0]
ud = core.DATA / "keeper" / "understood"
ud.mkdir(parents=True, exist_ok=True)
long = "a detail of the frames that is long enough to matter. " * 30
(ud / f"{s_any['id']}.json").write_text(json.dumps({"id": s_any["id"], "source_sig": qc._source_sig(s_any), "read_at": core.now(), "model": "m", "images_read": True,
    "reading": {"marked": long, "right_version": long, "product_and_scene": long, "admin_words": "WORDS-MARK " + long, "prompts_saved": "the flawed version's and the correct version's",
                "unclear": "OPEN-MARK", "frames_seen": [{"frame": "WRONG 0:01.00", "seen": "FRAME-MARK " + long}]}}), encoding="utf-8")
line = line_of(int(s_any["id"][1:]))
check("the digest carries only the short collection (three fields, each cut) and the path of the full one; the frames, the admin's words and what was unclear stay in the file",
      "READ (collected from its picture, no verdict" in line and "full collection:" in line and not any(m in line for m in ("WORDS-MARK", "OPEN-MARK", "FRAME-MARK"))
      and len(line.split("READ (collected from its picture", 1)[1].split("\n", 1)[0]) < 1000, line[-400:])
(ud / f"{s_any['id']}.json").unlink()

print("\n[the product images of a sample: which and how many its generation was given, by the client's original files (the admin, 2026-10-30)]")
pi_id, pi_mo = mk_miss("the label is wrong with one view", video="09")
ask()                                  # the new sample reaches the keeper; what follows must come from its product images alone
pi_meta_p = core.DATA / "samples" / str(pi_mo) / "meta.json"
pi_meta = json.loads(pi_meta_p.read_text(encoding="utf-8"))
pi_meta["product_samples"] = [{"asset": "A82", "label": "Image 2 · A82 · Carton-Front.png", "file": "image2-product-front.png", "image": "Image 2",
                               "original": "C:/client/carton/Carton-Front.png", "original_name": "Carton-Front.png", "prepared": "padded to 9:16 on white", "recorded": "source"}]
pi_meta_p.write_text(json.dumps(pi_meta), encoding="utf-8")
fresh, current, text = ask()
line = line_of(pi_mo)
check("a sample whose product images change re-syncs the keeper, with that sample's record alone", fresh and current and DELTA in text
      and list(qc.records_of(text)) == [f"S{pi_mo}"], text[:300])
check("its digest line says how many product images its generation was given and names each by its input, asset id and the client's original file "
      "name, never a folder", f"PRODUCT IMAGES OF S{pi_mo} (1): Image 2 · A82 · Carton-Front.png" in line and "C:/client" not in line, line[:400])
pi_meta["product_samples"].append({"asset": "A83", "label": "Image 3 · A83 · Carton-Back.png", "file": "image3-product-back.png", "image": "Image 3",
                                   "original": "C:/client/carton/Carton-Back.png", "original_name": "Carton-Back.png", "prepared": "", "recorded": "source"})
pi_meta_p.write_text(json.dumps(pi_meta), encoding="utf-8")
fresh, current, text = ask()
check("a second product image re-syncs it again, the count with it", fresh and current
      and f"PRODUCT IMAGES OF S{pi_mo} (2): Image 2 · A82 · Carton-Front.png; Image 3 · A83 · Carton-Back.png" in line_of(pi_mo), line_of(pi_mo)[:400])
check("the boot message explains the PRODUCT IMAGES section of a record", "PRODUCT IMAGES OF S.." in qc.LIB_HEADER)
pi_meta["images_sent"] = [{"asset": "A47", "label": "Image 1 · A47 · face.jpg", "file": "image1-character-face.jpg", "image": "Image 1", "kind": "character",
                           "original": "C:/client/people/face.jpg", "original_name": "face.jpg", "prepared": "a head crop", "recorded": "source"},
                          {"asset": "A82", "label": "Image 2 · A82 · Carton-Front.png", "file": "image2-product-front.png", "image": "Image 2", "kind": "product",
                           "original": "C:/client/carton/Carton-Front.png", "original_name": "Carton-Front.png", "prepared": "padded", "recorded": "source"}]
pi_meta_p.write_text(json.dumps(pi_meta), encoding="utf-8")
fresh, current, text = ask()
line = line_of(pi_mo)
check("what the generation was sent re-syncs the keeper: the product image under PRODUCT IMAGES, the character under OTHER IMAGES, never as a product",
      fresh and current and f"PRODUCT IMAGES OF S{pi_mo} (1): Image 2 · A82 · Carton-Front.png" in line
      and f"OTHER IMAGES OF S{pi_mo} (1): Image 1 · A47 · face.jpg (character)" in line and "Image 3 · A83" not in line, line[:500])
check("the boot message explains the OTHER IMAGES section too", "OTHER IMAGES OF S.." in qc.LIB_HEADER)
misses.delete(pi_id, "test") if hasattr(misses, "delete") else None
ask()

print("\n[the same library everywhere]")
same_library("at the end")
finish()
