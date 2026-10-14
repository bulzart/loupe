"""Loupe self-test: a real server on a free port, a temporary data folder and a fake pipeline run.  `python tests/selftest.py [--keeper]`"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import urllib.error
import urllib.request
from pathlib import Path

TMP = Path(tempfile.mkdtemp(prefix="loupe-test-"))
os.environ["LOUPE_DATA"] = str(TMP / "data")
os.environ["ATP_HOME"] = str(TMP / "atp")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from loupe_app import core, flags, media, runs, server  # noqa: E402

OK, BAD = [], []


def check(name, cond, extra=""):
    (OK if cond else BAD).append(name)
    print(("  ok   " if cond else "  FAIL ") + name + (f"   {extra}" if extra and not cond else ""))


def mkvideo(path: Path, color: str, box_color: str):
    path.parent.mkdir(parents=True, exist_ok=True)
    cp = subprocess.run([media.find_tool("ffmpeg"), "-y", "-loglevel", "error", "-f", "lavfi", "-i",
                         f"color=c={color}:s=720x1280:r=30:d=3,drawbox=x=200:y=500:w=220:h=220:color={box_color}:t=fill", "-pix_fmt", "yuv420p", str(path)],
                        capture_output=True)
    assert cp.returncode == 0, cp.stderr[-300:]


def build_fake_atp():
    run = TMP / "atp" / "runs" / "20261001-000000-07"
    mkvideo(run / "60-outputs" / "video-03" / "a1" / "raw.mp4", "0x305090", "red")
    mkvideo(run / "60-outputs" / "video-03" / "a2" / "raw.mp4", "0x305090", "green")
    (run / "30-assets" / "stage").mkdir(parents=True)
    (run / "30-assets" / "stage" / "pack.png").write_bytes(b"\x89PNG\r\n\x1a\n" + b"0" * 64)
    (run / "30-assets" / "uploads.json").write_text(json.dumps({"pack.png": {"media_id": "m1", "content_type": "image/png"}}))
    (run / "50-requests").mkdir()
    req = {"params": {"model": "wan3_0_prime", "prompt": "Show the can. Holding the glass of red wine near a celebrity banner. Keep the camera still.",
                      "medias": [{"role": "image_references", "value": "m1"}]}, "medias_meaning": {"Image 1": "pack.png"}}
    (run / "50-requests" / "video-03-request.json").write_text(json.dumps(req))
    (run / "state.json").write_text(json.dumps({"run_id": run.name, "mode": "analyze", "analyze_submode": "recreate", "model": "wan3_0_prime", "stage": "production",
        "videos": [{"video": "03", "status": "done", "qc": "regenerated once"}],
        "submissions": [{"video": "03", "lane": "03", "attempt": 1, "fingerprint": "fp1", "status": "failed", "flagged": True, "job_id": "job-111",
                         "request_file": str(run / "50-requests" / "video-03-request.json"),
                         "history": [{"at": "2026-10-14T10:00:00+02:00", "to": "submitted"}, {"at": "2026-10-14T10:00:16+02:00", "to": "failed"}]}]}))
    return run


class C:
    base = ""
    cookie = ""
    csrf = ""


def req(method, path, body=None, key=None, headers=None, raw=False, session=True):
    data = json.dumps(body).encode() if body is not None else None
    r = urllib.request.Request(C.base + path, data=data, method=method)
    if data is not None:
        r.add_header("Content-Type", "application/json")
    if key:
        r.add_header("Authorization", "Bearer " + key)
    elif session and C.cookie:
        r.add_header("Cookie", C.cookie)
        if method != "GET":
            r.add_header("X-Loupe-CSRF", C.csrf)
    for k, v in (headers or {}).items():
        r.add_header(k, v)
    try:
        with urllib.request.urlopen(r, timeout=60) as resp:
            b = resp.read()
            return resp.status, (b if raw else json.loads(b.decode())), resp.headers
    except urllib.error.HTTPError as e:
        b = e.read()
        try:
            return e.code, json.loads(b.decode()), e.headers
        except ValueError:
            return e.code, b, e.headers


def main(with_keeper=False):
    build_fake_atp()
    httpd = server.ThreadingHTTPServer((core.HOST, 0), server.Handler)
    httpd.daemon_threads = True
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    C.base = f"http://127.0.0.1:{httpd.server_port}"
    print("server", C.base, "data", TMP)

    print("\n[auth]")
    s, d, _ = req("GET", "/api/health")
    check("health answers without a key", s == 200 and d["app"] == "loupe" and d["admin_set"] is False)
    s, d, _ = req("GET", "/api/flags")
    check("no key, no session -> 401", s == 401)
    s, d, _ = req("GET", "/api/health", headers={"Host": "evil.example:80"})
    check("a foreign Host header is refused", s == 403)
    s, d, h = req("POST", "/api/setup", {"password": "short"})
    check("a short passphrase is refused", s == 400)
    s, d, h = req("POST", "/api/setup", {"password": "correct horse battery"})
    check("setup sets the passphrase and signs in", s == 200 and "loupe_session" in (h.get("Set-Cookie") or ""))
    C.cookie = (h.get("Set-Cookie") or "").split(";")[0]
    C.csrf = d["csrf"]
    s, d, _ = req("POST", "/api/setup", {"password": "another passphrase"})
    check("setup cannot be repeated", s == 403)
    s, d, _ = req("POST", "/api/keys", {"name": "x", "scopes": ["admin"]}, headers={"X-Loupe-CSRF": "wrong"})
    check("CSRF header is enforced for the session", s == 403)
    s, d, _ = req("POST", "/api/keys", {"name": "pipeline", "scopes": ["flags:write", "flags:read", "runs:read"]})
    kp = d["key"]
    s, d, _ = req("POST", "/api/keys", {"name": "qc", "scopes": ["qc:read"]})
    kq = d["key"]
    check("API keys are created and shown once", kp.startswith("lk_") and kq.startswith("lk_"))
    s, d, _ = req("GET", "/api/keys")
    check("the key list never contains a key", all("key" not in k and "hash" not in k for k in d["keys"]))

    print("\n[scopes]")
    s, _, _ = req("GET", "/api/misses", key=kq)
    check("a qc key cannot read the admin area", s == 403)
    s, _, _ = req("POST", "/api/flags", {"title": "x"}, key=kq)
    check("a qc key cannot write flags", s == 403)
    s, _, _ = req("GET", "/api/qc/samples", key=kp)
    check("a flags key cannot read the QC samples", s == 403)
    s, _, _ = req("GET", "/api/flags", key="lk_nope")
    check("an unknown key -> 401", s == 401)

    print("\n[flags]")
    prompt = "Show the can. Holding the glass of red wine near a celebrity banner. Keep the camera still."
    doc = {"title": "Refused within 16 s, no reason", "kind": "refused-no-reason", "status": "open",
           "where": {"run": "20261001-000000-07", "video": "03", "attempt": "a1", "model": "wan3_0_prime", "mode": "analyze-recreate"},
           "response": "none given", "job": "job-111", "fingerprint": "fp1", "prompt": prompt, "params": {"model": "wan3_0_prime"},
           "inputs": [{"label": "Image 1", "role": "image_references", "name": "pack.png", "facts": {"sha1": "abc", "text_seen": ["CELSIUS", "@someone"]}}],
           "trigger": {"text": "celebrity banner", "evidence": "I", "tags": ["celebrity-name"], "assets": ["Image 1"]}}
    s, d, _ = req("POST", "/api/flags", doc, key=kp)
    fid = d.get("id")
    check("a flag is recorded with a server-assigned id", s == 200 and fid == "CF-001", str(d)[:200])
    ev = d["entry"]["evidence"]
    check("timing evidence is read from the run ledger (16 s)", ev["timing"].get("seconds_to_fail") == 16, str(ev.get("timing")))
    check("signals are plain evidence statements", ev["signals"] and all(x["basis"] == "E" for x in ev["signals"]))
    check("on-screen text becomes a signal", any("CELSIUS" in x["text"] for x in ev["signals"]))
    s, d, _ = req("PATCH", f"/api/flags/{fid}", {"status": "cleared", "outcome": "cleared: changed request accepted",
                                                  "cleared": {"prompt": "Show the can. Keep the camera still.", "job": "job-222"}}, key=kp)
    ev = d["entry"]["evidence"]
    check("clearing a flag computes what changed", bool(ev.get("diff_to_cleared")) and "wine" in ev["diff_to_cleared"]["words_removed"],
          str(ev.get("diff_to_cleared"))[:200])
    s, d, _ = req("GET", "/api/flags?q=status:cleared+celebrity", key=kp)
    check("field search finds it", d["total"] == 1)
    s, d, _ = req("GET", "/api/flags?q=status:open", key=kp)
    check("the filter excludes it", d["total"] == 0)
    s, d, _ = req("GET", "/api/flags-stats", key=kp)
    check("stats are served", d["total"] == 1 and d["by_status"].get("cleared") == 1)
    # a queued write (Loupe was down) is ingested
    from loupe_app.flags import ingest_outbox
    (core.DATA / "outbox").mkdir(parents=True, exist_ok=True)
    (core.DATA / "outbox" / "t.json").write_text(json.dumps({"op": "add", "doc": {"id": "CF-Qabc123", "title": "queued while down", "kind": "other"}}))
    check("the offline outbox is ingested", ingest_outbox() == 1 and flags.get("CF-002")["title"] == "queued while down")

    print("\n[runs and media]")
    s, d, _ = req("GET", "/api/runs", key=kp)
    check("runs are listed", s == 200 and d["runs"][0]["run"] == "20261001-000000-07" and d["runs"][0]["model"] == "wan3_0_prime")
    s, d, _ = req("GET", "/api/runs/20261001-000000-07", key=kp)
    vers = d["videos"][0]["versions"]
    check("a video's versions (attempts) are found", [v["label"] for v in vers] == ["a1 · raw.mp4", "a2 · raw.mp4"], str([v["label"] for v in vers]))
    wrong, fixed = vers[0]["path"], vers[1]["path"]
    from urllib.parse import quote
    s, d, _ = req("GET", "/api/video?path=" + quote("C:/Windows/win.ini"))
    check("a file outside the allowed folders is refused", s in (403, 404))
    s, d, _ = req("GET", "/api/video?path=" + quote(str(TMP / "atp" / "runs" / ".." / ".." / "x.mp4")))
    check("path traversal is refused", s in (403, 404))
    s, b, h = req("GET", "/api/video?path=" + quote(wrong), headers={"Range": "bytes=0-99"}, raw=True)
    check("video streaming supports Range (206)", s == 206 and len(b) == 100 and h.get("Content-Range", "").startswith("bytes 0-99/"))
    s, b, h = req("GET", "/api/frame?path=" + quote(wrong) + "&t=1.5", raw=True)
    check("an exact frame is served as PNG", s == 200 and b[:4] == b"\x89PNG")

    print("\n[misses and samples]")
    s, d, _ = req("POST", "/api/misses", {"run": "20261001-000000-07", "video": "03", "title": "Red box where the green one belongs",
                                          "explanation": "The product was drawn in the wrong colour after the cut.", "kinds": ["continuity", "label-orientation"],
                                          "model": "wan3_0_prime", "mode": "analyze-recreate", "product": "test pack", "wrong_path": wrong, "fixed_path": fixed})
    mid = d["miss"]["id"]
    check("a miss report is created", s == 200 and mid == 1, str(d)[:200])
    shapes = [{"type": "arrow", "x1": 0.1, "y1": 0.25, "x2": 0.38, "y2": 0.42}, {"type": "circle", "cx": 0.4, "cy": 0.47, "rx": 0.18, "ry": 0.09},
              {"type": "text", "x": 0.05, "y": 0.2, "text": "wrong colour"}]
    s, d, _ = req("POST", f"/api/misses/{mid}/moments", {"t_wrong": 1.0, "t_fixed": 1.0, "shapes": shapes,
                                                          "details": "The pack is red here.", "correct_note": "It is green in the fixed version."})
    mo = d["moment"]
    check("a moment builds the sample image", s == 200 and mo["id"] == "S1", str(d)[:200])
    img = core.DATA / "samples" / "1" / "composite.png"
    from PIL import Image
    im = Image.open(img)
    check("the composite holds both frames (wide, tall) plus zoom and text", im.width > 1100 and im.height > 1500, str(im.size))
    px_left = Image.open(core.DATA / "samples" / "1" / "annotated.png").convert("RGB").getpixel((int(.38 * 720), int(.42 * 1280)))
    check("the annotation is drawn on the wrong frame (arrow tip is red/white)", px_left[0] > 200, str(px_left))
    s, b, h = req("GET", "/api/qc/samples/S1/image", key=kq, raw=True)
    check("QC reads the composite with its key", s == 200 and b[:4] == b"\x89PNG")
    s, d, _ = req("GET", "/api/qc/samples?model=wan3_0&kinds=continuity", key=kq)
    check("QC search ranks by model family and kind", d["samples"] and d["samples"][0]["id"] == "S1" and d["samples"][0]["score"] >= 5, "score=" + str(d["samples"][0].get("score") if d["samples"] else None) + " kinds=" + str(d["samples"][0].get("kinds") if d["samples"] else None))
    s, d, _ = req("POST", "/api/qc/brief", {"model": "wan3_0_prime", "q": "the pack colour changes after a cut"}, key=kq)
    check("the deterministic brief lists the sample with its image path", "S1" in d["text"] and "composite.png" in d["text"])
    s, d, _ = req("PATCH", f"/api/moments/1", {"details": "Changed words", "status": "archived"})
    s, d, _ = req("GET", "/api/qc/samples", key=kq)
    check("an archived sample leaves the QC library", d["library_size"] == 0)
    s, d, _ = req("PATCH", f"/api/moments/1", {"status": "active"})
    if with_keeper:
        print("\n[keeper] (a real headless session)")
        s, d, _ = req("POST", "/api/qc/consult", {"run": "r", "video": "03", "model": "wan3_0_prime", "question": "The product changes colour after a cut. Which samples apply?"}, key=kq)
        check("the keeper answers from its library", s == 200 and d.get("mode") == "keeper" and any(r["id"] == "S1" for r in d.get("relevant", [])), str(d)[:400])
        s, d, _ = req("POST", "/api/qc/consult", {"run": "r", "video": "03", "question": "Same question again, brief."}, key=kq)
        check("a second consult resumes the same session", s == 200 and d.get("mode") == "keeper")
        s, d, _ = req("GET", "/api/qc/keeper")
        check("the keeper session is recorded and read-only", d["keeper"].get("session_id") and d["keeper"]["turns"] >= 3)
    print("\n[cleanup]")
    httpd.shutdown()
    shutil.rmtree(TMP, ignore_errors=True)
    print(f"\n{len(OK)} passed, {len(BAD)} failed")
    if BAD:
        print("FAILED:", ", ".join(BAD))
    sys.exit(1 if BAD else 0)


if __name__ == "__main__":
    main("--keeper" in sys.argv)
