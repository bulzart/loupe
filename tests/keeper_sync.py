"""Is the QC keeper in sync with the app?  A deterministic check, no model call: the keeper session is replaced by a recorder, and after every kind of change to
the library (a sample added, edited, archived, restored, deleted; a kind changed; a frame added) the next question the keeper receives must carry the current
library again, while an unchanged library must not resend it.  python tests/keeper_sync.py"""
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

TMP = Path(tempfile.mkdtemp(prefix="loupe-sync-"))
os.environ["LOUPE_DATA"] = str(TMP / "data")
os.environ["ATP_HOME"] = str(TMP / "atp")
os.environ["ATG_HOME"] = str(TMP / "atg")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from loupe_app import core, media, misses, qc  # noqa: E402

core.ensure_dirs()
OK, BAD = [], []


def check(name, cond, extra=""):
    (OK if cond else BAD).append(name)
    print(("  ok   " if cond else "  FAIL ") + name + (f"   {extra}" if extra and not cond else ""))


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


def mk_miss(title, video="03", kinds=None):
    m = misses.create({"run": CTX["run"], "video": video, "title": title, "model": "wan3_0_prime", "kinds": kinds or ["continuity"], "wrong_path": str(a1), "fixed_path": str(a2)}, "test")
    mo = misses.add_moment(m["id"], {"frames": [{"src": "wrong", "role": "wrong", "t": 1.0}, {"src": "fixed", "role": "right", "t": 1.0}], "details": "the can is small here"}, "test")
    return m["id"], mo["moment_id"]


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

print(f"\n{len(OK)} passed, {len(BAD)} failed")
if BAD:
    print("FAILED: " + "; ".join(BAD))
sys.exit(1 if BAD else 0)
