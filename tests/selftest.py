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
os.environ["ATG_HOME"] = str(TMP / "atg")
os.environ["AWL_HOME"] = str(TMP / "awl")
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
    ref = TMP / "reference-folder"
    ref.mkdir()
    from PIL import Image as _I
    _I.new("RGBA", (400, 1000), (0, 0, 255, 255)).save(ref / "product-front.png")
    (run / "30-assets" / "project").mkdir(parents=True)
    (run / "30-assets" / "project" / "asset-inventory.json").write_text(json.dumps({"items": [{"id": "A14", "path": str(ref / "product-front.png"), "rel": "product-front.png"},
                                                                                           {"id": "A28", "path": str(ref / "person.png"), "rel": "person.png"}]}))
    req = {"params": {"model": "wan3_0_prime", "prompt": "Show the can. Holding the glass of red wine near a celebrity banner. Keep the camera still.",
                      "medias": [{"role": "image_references", "value": "m1"}]}, "medias_meaning": {"Image 1": "pack.png", "Image 2": "Test can, sealed: A14", "Image 3": "CH-M01 identity: A28 cropped above the collar"}}
    (run / "50-requests" / "video-03-request.json").write_text(json.dumps(req))
    (run / "state.json").write_text(json.dumps({"run_id": run.name, "mode": "analyze", "analyze_submode": "recreate", "model": "wan3_0_prime", "stage": "production",
        "assets_paths": [str(ref)], "assets": {"products": {"P1": {"canonical": "A14", "files": ["A14"]}, "P2": {"canonical": "A99", "unused": True}}},
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
    check("runs are listed", s == 200 and d["runs"][0]["run"] == "20261001-000000-07" and d["runs"][0]["model"] == "wan3_0_prime" and d["runs"][0]["lib"] == "atp")
    atg_run = TMP / "atg" / "runs" / "20261002-000000-03"
    mkvideo(atg_run / "60-outputs" / "video-01" / "a1" / "raw.mp4", "0x305090", "red")
    (atg_run / "state.json").write_text(json.dumps({"run_id": atg_run.name, "mode": "analyze", "analyze_submode": "create", "model": "seedance_2_5", "stage": "production",
                                                    "videos": [{"video": "01", "status": "done", "qc": "ok"}]}))
    s, d, _ = req("GET", "/api/runs", key=kp)
    libs = {r["run"]: r["lib"] for r in d["runs"]}
    check("runs of both pipeline libraries are listed, newest name first", s == 200 and libs.get("20261002-000000-03") == "atg" and libs.get("20261001-000000-07") == "atp"
          and [r["run"] for r in d["runs"]][:2] == ["20261002-000000-03", "20261001-000000-07"], str(libs))
    s, d, _ = req("GET", "/api/runs/20261002-000000-03", key=kp)
    check("a run of the second library resolves with its video versions", s == 200 and d["videos"][0]["versions"] and d["videos"][0]["versions"][0]["label"] == "a1 · raw.mp4", str(d)[:200])
    check("both libraries' runs folders are readable media roots", all(Path(str(lib / "runs")).resolve() in media.allowed_roots() for _n, lib in core.LIBS))
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
    px_left = Image.open(core.DATA / "samples" / "1" / "annotated.png").convert("RGB").getpixel((int(.38 * 720) - 9, int(.42 * 1280) - 10))     # inside the arrowhead, just behind its tip
    check("the annotation is drawn on the wrong frame (arrow tip is red/white)", px_left[0] > 200, str(px_left))
    # the explanation is burned in the bottom centre, on a dark band
    W_, H_ = im.size
    rgb = im.convert("RGB")
    band = rgb.crop((0, H_ - 150, W_ // 2 - 10, H_))       # the left half: the explanation shares the band with the product sample
    light = [(x, y) for y in range(0, band.height, 2) for x in range(0, band.width, 2) if sum(band.getpixel((x, y))) > 600]
    check("the composite ends in a dark caption band", sum(rgb.getpixel((5, H_ - 3))) < 120, str(rgb.getpixel((5, H_ - 3))))
    mean_x = sum(x for x, _ in light) / max(1, len(light))
    check("the explanation is burned in the left half of the band and centred there", len(light) > 80 and abs(mean_x - W_ / 4) < W_ * 0.1, f"{len(light)} light px, mean x {mean_x:.0f} of {W_}")
    pr = rgb.getpixel((W_ * 3 // 4, H_ - 150))
    check("the product sample the video used fills the right half of the band", pr[2] > 200 and pr[0] < 80 and pr[1] < 80, str(pr))
    check("the right half sits on a quiet ground", rgb.getpixel((W_ // 2 + 40, H_ - 280)) == (206, 209, 215) or rgb.getpixel((W_ // 2 + 40, H_ - 40)) == (206, 209, 215), str(rgb.getpixel((W_ // 2 + 40, H_ - 280))))
    # marks on the fixed frame too (green), kept per side
    shapes2 = [{"type": "circle", "side": "wrong", "cx": 0.4, "cy": 0.5, "rx": 0.1, "ry": 0.06}, {"type": "arrow", "side": "fixed", "x1": 0.2, "y1": 0.3, "x2": 0.5, "y2": 0.5}]
    s, d, _ = req("POST", f"/api/misses/{mid}/moments", {"t_wrong": 2.0, "t_fixed": 2.0, "shapes": shapes2, "details": "Second moment: the pack changes size.", "correct_note": ""})
    mo2 = d["moment"]
    check("marks keep the frame they were drawn on", s == 200 and [x["frame"] for x in mo2["shapes"]] == [0, 1], str(d)[:200])
    check("an older request becomes a miss frame and a right frame, each with its own time", [(f["src"], f["role"], f["t"]) for f in mo2["frames"]] == [("wrong", "wrong", 2.0), ("fixed", "right", 2.0)], str(mo2["frames"]))
    imf = Image.open(core.DATA / "samples" / str(mo2["moment_id"]) / "annotated_fixed.png").convert("RGB")
    tx, ty = int(.5 * imf.width), int(.5 * imf.height)
    from PIL import ImageChops
    S1 = core.DATA / "samples" / "1"
    check("nothing is drawn on the right frame the admin did not mark", ImageChops.difference(Image.open(S1 / "annotated_fixed.png").convert("RGB"), Image.open(S1 / "fixed.png").convert("RGB")).getbbox() is None)
    check("the fixed frame carries its own green mark", any(g > 140 and r < 70 and b < 110 for r, g, b in (imf.getpixel((tx + dx, ty + dy)) for dx in range(-40, 1, 4) for dy in range(-40, 1, 4))))
    # any number of frames, each from either video, each with its own time, each called WRONG or RIGHT by the admin
    three = [{"src": "wrong", "role": "right", "t": 0.5}, {"src": "wrong", "role": "wrong", "t": 2.5}, {"src": "fixed", "role": "right", "t": 3.25}]
    s, d, _ = req("POST", f"/api/misses/{mid}/moments", {"frames": three, "shapes": [{"type": "rect", "frame": 1, "x1": .2, "y1": .3, "x2": .6, "y2": .5}], "details": "The pack is right early and wrong later."})
    m3 = d["moment"]
    dir3 = core.DATA / "samples" / str(m3["moment_id"])
    im3 = Image.open(dir3 / "composite.png").convert("RGB")
    check("a moment keeps three frames with their own times and roles", s == 200 and m3["n_frames"] == 3 and [(f["role"], f["t"]) for f in m3["frames"]] == [("right", 0.5), ("wrong", 2.5), ("right", 3.25)], str(d)[:300])
    check("three frames sit side by side", im3.width > 1700, str(im3.size))
    tags = [im3.getpixel((27 + k * 584, 55)) for k in range(3)]
    check("each frame has its own tag: green, red, green", tags[0][1] > 140 and tags[0][0] < 70 and tags[1][0] > 200 and tags[1][1] < 80 and tags[2][1] > 140 and tags[2][0] < 70, str(tags))
    check("the first frame called RIGHT is saved as the right frame, the first called WRONG as the wrong one", (dir3 / "fixed.png").exists() and (dir3 / "wrong.png").exists() and all((dir3 / f"frame-{k}-marked.png").exists() for k in (1, 2, 3)))
    check("the sample is not a miss example only because a frame is right", m3["has_right"] is True)
    m3_h = im3.height
    five = three + [{"src": "wrong", "role": "wrong", "t": 2.75}, {"src": "wrong", "role": "wrong", "t": 3.0}]
    s, d, _ = req("PATCH", f"/api/moments/{m3['moment_id']}", {"frames": five})
    im5 = Image.open(dir3 / "composite.png").convert("RGB")
    check("five frames wrap into two rows", s == 200 and d["moment"]["n_frames"] == 5 and im5.height > m3_h + 900 and im5.width == im3.width, f"{im5.size} vs {im3.size}")
    s, d, _ = req("PATCH", f"/api/moments/{m3['moment_id']}", {"frames": five[:1]})
    check("removing frames drops their marks", s == 200 and d["moment"]["n_frames"] == 1 and d["moment"]["shapes"] == [], str(d["moment"]["shapes"]))
    s, d, _ = req("PATCH", f"/api/moments/{m3['moment_id']}", {"frames": []})
    check("a moment may have no frame at all (a scenario in words only)", s == 200 and d["moment"]["n_frames"] == 0 and d["moment"]["shapes"] == [] and d["moment"]["sample_kind"] == "scenario", str(d)[:300])
    check("and its image is a card of words (the width of two panels, no frames)", Image.open(dir3 / "composite.png").width == 1192 and not (dir3 / "fixed.png").exists() and not (dir3 / "wrong.png").exists())
    s, d, _ = req("DELETE", f"/api/moments/{m3['moment_id']}")
    s, d, _ = req("DELETE", f"/api/moments/{mo2['moment_id']}")
    s, d, _ = req("GET", "/api/product-sample?run=20261001-000000-07&video=03&wrong=" + quote("a1 · raw.mp4"))
    check("the product the video used is found from its request, the registry and the inventory", s == 200 and [x["asset_id"] for x in d["samples"]] == ["A14"] and "Test can" in d["samples"][0]["label"], str(d)[:300])
    s, b, h = req("GET", d["samples"][0]["image_url"], raw=True)
    check("its preview is served as a PNG", s == 200 and b[:4] == b"\x89PNG")
    s, d, _ = req("GET", "/api/product-sample?run=20261001-000000-07&video=09&wrong=a1")
    check("a video whose request names no product gets no sample", s == 200 and d["samples"] == [])
    s, d, _ = req("PATCH", "/api/misses/1", {"product_sample": False})
    im_off = Image.open(core.DATA / "samples" / "1" / "composite.png").convert("RGB")
    light_off = [(x, y) for y in range(0, 150, 2) for x in range(0, im_off.width, 2) if sum(im_off.getpixel((x, im_off.height - 150 + y))) > 600]
    mean_off = sum(x for x, _ in light_off) / max(1, len(light_off))
    check("with the sample switched off the description takes the full width again", s == 200 and d["miss"]["product_sample"] is False and abs(mean_off - im_off.width / 2) < im_off.width * 0.12 and im_off.getpixel((im_off.width * 3 // 4, im_off.height - 150))[2] < 100, f"mean x {mean_off:.0f} of {im_off.width}")
    s, d, _ = req("PATCH", "/api/misses/1", {"product_sample": True})
    check("and back on", s == 200 and d["miss"]["product_sample"] is True)
    s, b, h = req("GET", "/api/qc/samples/S1/image", key=kq, raw=True)
    check("QC reads the composite with its key", s == 200 and b[:4] == b"\x89PNG")
    s, d, _ = req("GET", "/api/qc/samples?model=wan3_0&kinds=continuity", key=kq)
    check("QC search ranks by model family and kind", d["samples"] and d["samples"][0]["id"] == "S1" and d["samples"][0]["score"] >= 5, "score=" + str(d["samples"][0].get("score") if d["samples"] else None) + " kinds=" + str(d["samples"][0].get("kinds") if d["samples"] else None))
    s, d, _ = req("POST", "/api/qc/brief", {"model": "wan3_0_prime", "q": "the pack colour changes after a cut"}, key=kq)
    check("the deterministic brief lists the sample with its image path", "S1" in d["text"] and "composite.png" in d["text"])
    s, d, _ = req("PATCH", f"/api/moments/1", {"details": "Changed words", "status": "archived"})
    s, d, _ = req("GET", "/api/qc/samples", key=kq)
    check("an archived sample leaves the QC library", d["library_size"] == 0)
    s, d, _ = req("POST", "/api/qc/consult", {"purpose": "plan", "model": "wan3_0_prime", "question": "Beat 1: he lifts the can."}, key=kq)
    check("planning with an empty library answers at once, no keeper session", s == 200 and d.get("mode") == "empty" and d.get("hints") == [])
    # the rules held in code: only real ids, only high or medium, only a compatible model (unless model-independent), at most three, short text
    from loupe_app import qc as _qc
    lib = {f"S{i}": {"id": f"S{i}", "model": m} for i, m in ((1, "wan3_0_prime"), (2, "seedance_2_5"), (3, ""), (4, "wan3_0"), (5, "wan3_0_prime"), (6, "wan3_0_prime"))}
    mk = lambda i, conf="high", indep=False: {"id": f"S{i}", "trigger": "t" * 400, "right_outcome": "o", "lever": "l", "caveat": "c", "confidence": conf, "model_independent": indep}
    hints, held = _qc.plan_hints_from({"hints": [mk(1), mk(2), mk(3, "low"), mk(9), mk(4, "medium"), mk(5), mk(6)]}, {"model": "wan3_0_prime"}, lib)
    check("planning hints drop a low confidence one, an unknown id and a model that does not fit, and stop at three", [h["id"] for h in hints] == ["S1", "S5", "S6"] and held == 4, f"{[h['id'] for h in hints]} held {held}")
    hints, _h = _qc.plan_hints_from({"hints": [mk(2, "high", True)]}, {"model": "wan3_0_prime"}, lib)
    check("a model-independent miss crosses models", [h["id"] for h in hints] == ["S2"])
    check("hint text is cut at a sentence or a word (never mid-word, at most 600 characters) and carries no image path", all(len(h["trigger"]) <= 603 and "composite_path" not in h for h in hints))
    long_h = _qc.plan_hints_from({"hints": [dict(mk(1), caveat="Does not apply when a hand lifts the can. " * 20)]}, {"model": "wan3_0_prime"}, lib)[0][0]
    check("a hint's caveat is cut at a sentence end, so the condition that decides whether it applies stays whole", long_h["caveat"].endswith("can. …") and len(long_h["caveat"]) <= 503, long_h["caveat"][-40:])
    ok_h = {"id": "S1", "trigger": "t", "right_outcome": "the count reads three after the reveal", "lever": "restate the count once at that beat", "caveat": "c", "confidence": "high"}
    bad = [dict(ok_h, lever="Never lay the pack on the floor"), dict(ok_h, right_outcome="always keep it on the desk"), dict(ok_h, trigger="avoid the desk"), dict(ok_h, lever="the pack must stand")]
    hints, held = _qc.plan_hints_from({"hints": bad + [ok_h]}, {"model": "wan3_0_prime"}, lib)
    check("a hint worded as a universal command is held back, a condition is kept", [h["id"] for h in hints] == ["S1"] and held == 4, f"{[h['lever'] for h in hints]} held {held}")
    s2, d2, _ = req("POST", "/api/qc/consult", {"purpose": "plan", "model": "wan3_0_prime", "product_details": "flat sachet, 6 x 9 cm, tear notch at the top", "question": "Beat 1: he sets the sachet down."}, key=kq)
    check("a consult carries the product's details without trouble", s2 == 200)
    s, d, _ = req("GET", "/api/qc/keeper/state", key=kq)
    check("the keeper's state (in sync, turns, library version) is readable with the qc key, without the consult log or the session id",
          s == 200 and "in_sync" in d and "consults" not in d and "session_id" not in d, str(d)[:150])
    print("\n[the prompts of the two takes: in the record, never in the image, never diffed]")
    req("PATCH", "/api/moments/1", {"status": "active"})       # S1 was archived above; it is active for this block and archived again after it
    img1, meta1 = core.DATA / "samples" / "1" / "composite.png", core.DATA / "samples" / "1" / "meta.json"
    before_img, before_meta = img1.read_bytes(), meta1.read_text(encoding="utf-8")
    s, d, _ = req("PATCH", "/api/misses/1", {"wrong_prompt": "PROMPT-A the can on the desk.", "fixed_prompt": "PROMPT-B the can on the desk, as tall as the mug.", "prompt_note": "the height was stated against the mug"})
    check("the prompts of both takes and the admin's note are kept on the report", s == 200 and d["miss"]["wrong_prompt"].startswith("PROMPT-A") and d["miss"]["fixed_prompt"].startswith("PROMPT-B") and d["miss"]["prompts"] == "pair", str(d)[:200])
    check("they never touch the sample image or its meta (nothing rebuilt, nothing burned in)", img1.read_bytes() == before_img and meta1.read_text(encoding="utf-8") == before_meta and "PROMPT-A" not in before_meta)
    s, d, _ = req("GET", "/api/qc/samples?model=wan3_0_prime", key=kq)
    s1 = next(x for x in d["samples"] if x["id"] == "S1")
    check("the QC list says the sample has a prompt pair and carries the note, never the prompts", s1["prompts"] == "pair" and s1["prompt_note"].startswith("the height") and "wrong_prompt" not in s1 and "fixed_prompt" not in s1, str(sorted(s1))[:200])
    s, d, _ = req("GET", "/api/qc/samples/S1", key=kq)
    check("one sample without ?prompts=1 carries no prompt text either", s == 200 and "wrong_prompt" not in d["sample"] and d["sample"]["prompts"] == "pair")
    s, d, _ = req("GET", "/api/qc/samples/S1?prompts=1", key=kq)
    check("one sample with ?prompts=1 carries both prompts whole (the agent reads them and judges what changed; nothing is diffed here)", s == 200 and d["sample"]["wrong_prompt"].startswith("PROMPT-A") and d["sample"]["fixed_prompt"].startswith("PROMPT-B") and "diff" not in d["sample"] and "prompt_change" not in d["sample"])
    # the roles (the user, 2026-10-28): the judge gets both prompts whole from the record; the Haiku collector never sees them and gives no verdict
    from loupe_app import qc as _qcr
    longp = "PROMPT-A the can on the desk. " + "A long beat of the plan with the can on the desk. " * 200
    req("PATCH", "/api/misses/1", {"wrong_prompt": longp})
    (TMP / "fulfil").mkdir(exist_ok=True)
    got = _qcr._fulfil({"kind": "sample_origin", "id": "S1"}, {}, TMP / "fulfil", False)
    check("sample_origin hands the judge both prompts whole from the record, however long (no cut)", got.get("ok") and longp.strip() in got.get("text", "")
          and "PROMPT-B" in got.get("text", ""), str(got)[:200])
    ep = _qcr._extract_prompt(next(x for x in _qcr.library() if x["id"] == "S1"))
    check("the data collector is never given the prompts of the takes and is told to give no verdict", "PROMPT-A" not in ep and "PROMPT-B" not in ep
          and "give NO verdict" in _qcr.EXTRACT_ROLE and "prompt" not in _qcr.EXTRACT_ROLE.split("Answer ONE JSON")[1], ep[:200])
    check("the judge runs on Opus 5.5 and the collector on Haiku 5.5 unless the admin sets another model", _qcr.DEFAULT_MODEL == "claude-opus-5-5"
          and _qcr.EXTRACT_MODEL_DEFAULT == "claude-haiku-5-5")
    req("PATCH", "/api/misses/1", {"wrong_prompt": "PROMPT-A the can on the desk."})
    s, d, _ = req("POST", "/api/qc/brief", {"model": "wan3_0_prime"}, key=kq)
    check("the brief says the prompts are kept and where to read them, without their text", "prompts of both takes are kept" in d["text"] and "?prompts=1" in d["text"] and "PROMPT-A" not in d["text"], d["text"][:300])
    # a still asked for beside the video (a guide plate, a start frame) and a second attempt: neither is taken for the first attempt's prompt
    atp_run = TMP / "atp" / "runs" / "20261001-000000-07"
    (atp_run / "50-requests" / "video-03-g1-request.json").write_text(json.dumps({"tool": "generate_image", "params": {"model": "an_image_model", "prompt": "PLATE: the empty scene with a real can.", "medias": [{"role": "image_references", "value": "m1"}]}}))
    (atp_run / "50-requests" / "video-03-frame-a2-request.json").write_text(json.dumps({"params": {"model": "an_image_model", "prompt": "FRAME: edit the start frame.", "resolution": "2k", "medias": [{"role": "image_references", "value": "m1"}]}}))
    (atp_run / "50-requests" / "video-03-a2-request.json").write_text(json.dumps({"params": {"model": "wan3_0_prime", "duration": 5, "prompt": "Show the can, second attempt.", "medias": [{"role": "image_references", "value": "m1"}]}}))
    shutil.copyfile(atp_run / "60-outputs" / "video-03" / "a2" / "raw.mp4", atp_run / "60-outputs" / "video-03" / "clean.mp4")
    mkvideo(atp_run / "60-outputs" / "video-03" / "capcut-preview.mp4", "0x502020", "blue")        # the preview (a burn: equal to no attempt's file)
    (atp_run / "deliverables").mkdir(exist_ok=True)
    shutil.copyfile(atp_run / "60-outputs" / "video-03" / "capcut-preview.mp4", atp_run / "deliverables" / "video-03-preview.mp4")
    mkvideo(atp_run / "60-outputs" / "video-03" / "stray.mp4", "0x205050", "yellow")                 # a file the run never recorded and no attempt holds
    _st = json.loads((atp_run / "state.json").read_text(encoding="utf-8"))
    _st["videos"][0].update({"clean": str(atp_run / "60-outputs" / "video-03" / "clean.mp4"), "preview": str(atp_run / "60-outputs" / "video-03" / "capcut-preview.mp4")})
    (atp_run / "state.json").write_text(json.dumps(_st), encoding="utf-8")
    s, d, _ = req("GET", f"/api/request-prompt?run=20261001-000000-07&video=03&path={quote(wrong)}")
    check("the form can fill a prompt from the run's request file of the version picked (read, not judged)", s == 200 and d.get("prompt", "").startswith("Show the can.") and d.get("request") == "video-03-request.json", str(d)[:200])
    s, d, _ = req("GET", "/api/request-prompt?run=20261001-000000-07&video=09&path=x")
    check("no request file: an empty prompt, no error", s == 200 and d.get("prompt") == "")
    print("\n[a picked version fills the prompt of its own attempt]")
    vs = {v["label"]: v for v in runs.video_versions("20261001-000000-07", "03")}
    check("the versions say which file the service generated, its attempt, and which attempt is the delivered one",
          vs["a1 · raw.mp4"]["role"] == "generated" and vs["a2 · raw.mp4"]["role"] == "generated" and vs["clean.mp4"]["role"] == "processed" and vs["deliverables/video-03-preview.mp4"]["role"] == "deliverable"
          and vs["a2 · raw.mp4"]["attempt"] == 2 and vs["a2 · raw.mp4"]["delivered"] is True and vs["a1 · raw.mp4"]["delivered"] is False, str({k: (v["role"], v.get("attempt"), v.get("delivered")) for k, v in vs.items()}))
    fill = lambda p: req("GET", f"/api/request-prompt?run=20261001-000000-07&video=03&path={quote(p)}")[1]
    d = fill(vs["a1 · raw.mp4"]["path"])
    check("the first attempt's pick fills the first attempt's prompt, never a guide plate's or a start frame's", d.get("request") == "video-03-request.json" and d["prompt"].startswith("Show the can. Holding") and d["attempt"] == 1, str(d)[:160])
    d = fill(vs["a2 · raw.mp4"]["path"])
    check("the second attempt's pick fills the second attempt's request", d.get("request") == "video-03-a2-request.json" and d["prompt"].startswith("Show the can, second") and d["attempt"] == 2, str(d)[:160])
    d = fill(vs["clean.mp4"]["path"])
    check("a clean video with no attempt in its name fills the request of the attempt it is a copy of", d.get("request") == "video-03-a2-request.json" and d["attempt"] == 2, str(d)[:160])
    d = fill(vs["deliverables/video-03-preview.mp4"]["path"])
    check("the run's recorded preview, and a deliverable that is a copy of it, fill the delivered attempt's request (read from the run's record, not guessed)",
          d.get("request") == "video-03-a2-request.json" and d["attempt"] == 2, str(d)[:160])
    d = fill(str(atp_run / "60-outputs" / "video-03" / "capcut-preview.mp4"))
    check("the recorded preview itself too", d.get("request") == "video-03-a2-request.json" and d["attempt"] == 2, str(d)[:160])
    d = fill(str(atp_run / "60-outputs" / "video-03" / "stray.mp4"))
    check("a file whose attempt cannot be told fills nothing and says why (never the latest attempt)", d.get("prompt") == "" and d.get("attempt") == 0 and "cannot be told" in d.get("note", ""), str(d)[:200])
    d = req("GET", "/api/request-prompt?run=20261001-000000-07&video=03&path=" + quote("a1 · raw.mp4"))[1]
    check("a label with an attempt word still picks that attempt", d.get("request") == "video-03-request.json", str(d)[:160])
    runs.index_usage(force=True)
    con = core.db()
    try:
        att = {r["attempt"] for r in con.execute("SELECT attempt FROM usage WHERE video='03'")}
    finally:
        con.close()
    check("the usage index reads a guide plate and a start frame as what they are, not as the first attempt", att == {"a1", "a2", "g1", "image"}, str(att))
    s, d, _ = req("PATCH", "/api/misses/1", {"fixed_prompt": ""})
    check("clearing one prompt leaves the other (the prompt of the wrong take only)", s == 200 and d["miss"]["prompts"] == "wrong")
    s, d, _ = req("PATCH", "/api/misses/1", {"fixed_prompt": "PROMPT-B the can on the desk, as tall as the mug."})
    req("PATCH", "/api/moments/1", {"status": "archived"})
    s, d, _ = req("GET", "/api/misses/suggest-kinds?text=" + quote("The can size changes after the cut and the lid opens by itself; the label reads mirrored"))
    check("kinds are suggested from the admin's words (a prefix match on the vocabulary's own words), for the admin to confirm",
          s == 200 and set(d.get("suggested", [])) >= {"scale-size", "openings-use", "label-orientation"} and "counts" not in d.get("suggested", []), str(d))
    s, d, _ = req("GET", "/api/misses/suggest-kinds?text=")
    check("no words, no suggestion", s == 200 and d.get("suggested") == [])
    import shutil as _shutil, sqlite3 as _sqlite3, tempfile as _tempfile
    from loupe_app import seed as _seed
    man = _seed.export()
    sdb = _seed.seed_path()
    scon = _sqlite3.connect(f"file:{sdb.as_posix()}?mode=ro", uri=True)
    try:
        empty = {t: scon.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in ("sessions", "api_keys", "settings", "captures", "audit", "consults", "usage", "filehash")}
        kept = scon.execute("SELECT COUNT(*) FROM misses").fetchone()[0]
    finally:
        scon.close()
    check("the seed export keeps the misses and moments and drops every session, key, setting, capture, audit row and consult", kept == man["tables"]["misses"] >= 1 and set(empty.values()) == {0} and (sdb.parent / "seed.json").is_file(), str(empty))
    check("a signed URL loses its query string before it is seeded", _seed._scrub("see https://s3.x/a.png?X-Amz-Signature=abc&x=1 and https://cdn.x/b.png") == "see https://s3.x/a.png?<signed-query-removed> and https://cdn.x/b.png")
    fresh = Path(_tempfile.mkdtemp(prefix="loupe-seed-"))
    (fresh / "seed").mkdir()
    _shutil.copyfile(sdb, fresh / "seed" / "loupe-seed.db")
    old_data = core.DATA
    core.DATA = fresh
    try:
        c2 = core.db()
        n_seeded = c2.execute("SELECT COUNT(*) FROM misses").fetchone()[0]
        c2.close()
    finally:
        core.DATA = old_data
    check("a data folder with no database starts from the seed, so a clone opens with the samples", n_seeded == kept and (fresh / "loupe.db").is_file())
    scon = _sqlite3.connect(f"file:{sdb.as_posix()}?mode=ro", uri=True)
    try:
        seeded_prompt = scon.execute("SELECT wrong_prompt FROM misses WHERE id=1").fetchone()[0]
    finally:
        scon.close()
    check("the seed carries the prompts of the two takes with the misses", str(seeded_prompt or "").startswith("PROMPT-A"))
    s, d, _ = req("PATCH", f"/api/moments/1", {"status": "active"})
    long_title = ("The can size changes drastically and unexpectedly without any logical reasoning, " * 8).strip()
    s, d, _ = req("POST", "/api/misses", {"run": "20261001-000000-07", "video": "04", "title": long_title, "model": "wan3_0_prime", "wrong_path": wrong})
    lm = d["miss"]
    s, d, _ = req("POST", f"/api/misses/{lm['id']}/moments", {"frames": [{"src": "wrong", "role": "wrong", "t": 1.0}]})
    long_meta = json.loads((core.DATA / "samples" / str(d["moment"]["moment_id"]) / "meta.json").read_text(encoding="utf-8"))
    check("a long title is kept whole (it was once cut at 200 characters) and reaches the image", len(lm["title"]) == len(long_title) > 600 and long_meta["title"] == long_title, f"{len(lm['title'])} of {len(long_title)}")
    s, d, _ = req("DELETE", f"/api/misses/{lm['id']}")
    # a report in words only: no video at all, no frame, the idea, what could be better and what the agents missed
    s, d, _ = req("POST", "/api/misses", {"run": "", "video": "", "title": "The plan wanted the can to stay in hand; the agents let it go", "model": "wan3_0_prime"})
    wm = d["miss"]
    check("a report needs no video at all", s == 200 and wm["wrong_path"] == "" and wm["fixed_path"] == "", str(d)[:200])
    s, d, _ = req("POST", f"/api/misses/{wm['id']}/moments", {"frames": [{"src": "wrong", "role": "wrong", "t": 1.0}]})
    check("a frame cannot come from a Video A that does not exist", s == 400, str(s))
    s, d, _ = req("POST", f"/api/misses/{wm['id']}/moments", {"frames": [], "idea": "The hand keeps the can until the last beat.", "better": "State the grip at every beat.",
                                                              "missed": "The agents read the hold as optional.", "details": "Words only."})
    sc = d["moment"]
    sdir = core.DATA / "samples" / str(sc["moment_id"])
    check("a moment with no frames is a scenario that carries the idea, what could be better and what was missed", s == 200 and sc["sample_kind"] == "scenario" and sc["n_frames"] == 0
          and sc["idea"].startswith("The hand keeps") and sc["better"].startswith("State the grip") and sc["missed"].startswith("The agents read"), str(sc)[:300])
    sim = Image.open(sdir / "composite.png").convert("RGB")
    check("its image holds the words on a dark band", s == 200 and sim.width == 1192 and sim.height > 150 and sum(sim.getpixel((5, sim.height - 3))) < 120, str(sim.size))
    s, d, _ = req("POST", "/api/qc/brief", {"q": "the agents let the can go out of the hand", "model": "wan3_0_prime"}, key=kq)
    check("QC sees it as a scenario with the three texts", "SCENARIO" in d["text"] and "THE AI AGENTS MISSED OR COULD NOT UNDERSTAND: The agents read the hold" in d["text"] and "IDEA (what was meant to happen)" in d["text"], d["text"][:400])
    s, d, _ = req("GET", "/api/qc/samples?limit=30", key=kq)
    check("and the QC list marks it", any(x["id"] == sc["id"] and x["sample_kind"] == "scenario" and x["missed"] for x in d["samples"]))
    s, d, _ = req("PATCH", f"/api/misses/{wm['id']}", {"wrong_path": wrong})
    s, d, _ = req("POST", f"/api/misses/{wm['id']}/moments", {"frames": [{"src": "wrong", "role": "example", "t": 1.0}], "shapes": [{"type": "rect", "frame": 0, "x1": .2, "y1": .3, "x2": .6, "y2": .5}], "idea": "Look at this frame."})
    nf = d["moment"]
    check("its role is saved as example", [f["role"] for f in nf["frames"]] == ["example"], str(nf["frames"]))
    s, d, _ = req("POST", f"/api/misses/{wm['id']}/moments", {"frames": [{"src": "wrong", "role": "neutral", "t": 1.0}]})
    check("the first name of the role (neutral) is still accepted and becomes example", s == 200 and d["moment"]["frames"][0]["role"] == "example", str(d)[:200])
    nimg = Image.open(core.DATA / "samples" / str(nf["moment_id"]) / "composite.png").convert("RGB")
    check("a frame can be kept as an Example (neither wrong nor right): it gets a blue EXAMPLE tag, with its close range beside it at the same height", nf["sample_kind"] == "scenario" and nimg.getpixel((27, 55)) == (59, 110, 220)
          and nimg.getpixel((611, 55)) == (70, 74, 90) and nimg.width == 1192, f"{nimg.getpixel((27, 55))} {nimg.getpixel((611, 55))}")
    s, d, _ = req("POST", f"/api/misses/{wm['id']}/moments", {"frames": [{"src": "wrong", "role": "wrong", "t": 1.0}, {"src": "wrong", "role": "wrong", "t": 1.5}, {"src": "wrong", "role": "wrong", "t": 2.0},
                                                                         {"src": "wrong", "role": "example", "t": 2.5}],
                                                              "shapes": [{"type": "rect", "frame": 0, "x1": .2, "y1": .3, "x2": .6, "y2": .5}]})
    four = Image.open(core.DATA / "samples" / str(d["moment"]["moment_id"]) / "composite.png").convert("RGB")
    check("four frames: three in a row with a zoom under the marked one only, and the fourth (unmarked) alone in its row with no close range", s == 200
          and four.getpixel((612, 1689)) == (59, 110, 220) and four.getpixel((1400, 1960)) == (245, 245, 247) and four.getpixel((27 + 584 * 2, 55)) == (255, 45, 45)
          and four.getpixel((304, 1384)) != (245, 245, 247) and four.getpixel((888, 1384)) == (245, 245, 247),
          f"{four.size} {four.getpixel((612, 1689))} {four.getpixel((1400, 1960))} {four.getpixel((304, 1384))} {four.getpixel((888, 1384))}")
    s, d, _ = req("PATCH", f"/api/misses/{wm['id']}", {"wrong_path": ""})
    s2, d2, _ = req("GET", f"/api/misses/{wm['id']}")
    check("clearing Video A drops the frames that came from it and leaves the words", s == 200 and all(x["n_frames"] == 0 for x in d2["miss"]["moments"]) and any(x["idea"] for x in d2["miss"]["moments"]))
    s, d, _ = req("DELETE", f"/api/misses/{wm['id']}")
    # a miss that has no right version stays as a miss example
    s, d, _ = req("POST", "/api/misses", {"run": "20261001-000000-07", "video": "04", "title": "The label is unreadable from the first frame", "explanation": "No fixed take exists.",
                                          "kinds": ["label-orientation"], "model": "wan3_0_prime", "mode": "analyze-recreate", "wrong_path": wrong})
    nid = d["miss"]["id"]
    check("a miss can be saved with no right version", s == 200 and d["miss"]["fixed_path"] == "", str(d)[:200])
    s, d, _ = req("POST", f"/api/misses/{nid}/moments", {"t_wrong": 1.0, "t_fixed": 1.0, "shapes": [{"type": "rect", "side": "wrong", "x1": .2, "y1": .3, "x2": .6, "y2": .5}],
                                                          "details": "The label reads as scribbles here."})
    nm = d["moment"]
    ndir = core.DATA / "samples" / str(nm["moment_id"])
    nim = Image.open(ndir / "composite.png").convert("RGB")
    check("the sample knows it has no right version", s == 200 and nm["has_right"] is False and not (ndir / "fixed.png").exists() and not (ndir / "annotated_fixed.png").exists())
    grey = lambda im: any(im.getpixel((x, 55)) == (96, 100, 112) for x in range(24, 900))      # noqa: E731
    check("a lone miss frame has its close range beside it and a grey NO RIGHT VERSION tag in the image", nim.getpixel((611, 55)) == (70, 74, 90) and grey(nim), str(nim.getpixel((611, 55))))
    s, d, _ = req("GET", "/api/qc/samples?limit=20", key=kq)
    check("QC sees that it is a miss example only", any(x["id"] == nm["id"] and x["has_right"] is False for x in d["samples"]) and any(x["has_right"] is True for x in d["samples"]))
    s, d, _ = req("POST", "/api/qc/brief", {"q": "label unreadable scribbles", "model": "wan3_0_prime"}, key=kq)
    check("the brief says there is no right version for it", "NO RIGHT VERSION" in d["text"])
    s, d, _ = req("PATCH", f"/api/misses/{nid}", {"fixed_path": fixed, "fixed_label": "client fix"})
    nim2 = Image.open(ndir / "composite.png").convert("RGB")
    check("a right version attached later rebuilds the saved moments", s == 200 and (ndir / "fixed.png").exists() and nim2.getpixel((611, 55)) == (22, 163, 74) and not grey(nim2))
    s, d, _ = req("POST", f"/api/misses/{nid}/moments", {"frames": [{"src": "wrong", "role": "right", "t": 0.5}, {"src": "wrong", "role": "wrong", "t": 3.0}, {"src": "fixed", "role": "right", "t": 3.0}], "details": "Two frames of the one video and one of the client fix."})
    kept = d["moment"]
    check("a frame of the same video can be the right one (early) with the miss later", s == 200 and kept["has_right"] is True and kept["n_frames"] == 3, str(d)[:200])
    s, d, _ = req("PATCH", f"/api/misses/{nid}", {"fixed_path": ""})
    s2, d2, _ = req("GET", f"/api/misses/{nid}")
    kv = next(x for x in d2["miss"]["moments"] if x["moment_id"] == kept["moment_id"])
    check("clearing the right version drops only the frames that came from it", [(f["src"], f["role"]) for f in kv["frames"]] == [("wrong", "right"), ("wrong", "wrong")] and kv["has_right"] is True, str(kv["frames"]))
    s, d, _ = req("POST", f"/api/misses/{nid}/moments", {"frames": [{"src": "fixed", "role": "right", "t": 1.0}]})
    check("a frame cannot come from a right version that does not exist", s == 400, str(s))
    s, d, _ = req("DELETE", f"/api/moments/{kept['moment_id']}")
    nim3 = Image.open(ndir / "composite.png").convert("RGB")
    check("clearing it makes the miss an example again", s == 200 and not (ndir / "fixed.png").exists() and nim3.getpixel((611, 55)) == (70, 74, 90) and grey(nim3))
    s, d, _ = req("DELETE", f"/api/misses/{nid}")
    print("\n[either side can be any video: the original from the run's records, a take of another video of the run; the tags say which]")
    atp_run = TMP / "atp" / "runs" / "20261001-000000-07"
    given = TMP / "client"                                   # the client's own folder, outside the run
    mkvideo(given / "source-clip.mp4", "0x205020", "white")
    mkvideo(atp_run / "10-media" / "V05" / "prepared" / "source.mp4", "0x206030", "white")      # the source as the request sent it to the model (prepared)
    (atp_run / "00-inventory").mkdir(exist_ok=True)
    (atp_run / "00-inventory" / "inventory.json").write_text(json.dumps({"items": [{"id": "V05", "kind": "video", "path": str(given / "source-clip.mp4")}]}))
    _rq = json.loads((atp_run / "50-requests" / "video-03-request.json").read_text(encoding="utf-8"))
    _rq.update({"reference": "V05 (the clip this video recreates)", "source_prepared": "10-media/V05/prepared/source.mp4: silent, as sent"})
    (atp_run / "50-requests" / "video-03-request.json").write_text(json.dumps(_rq))
    req("POST", "/api/settings", {"allowed_roots": [str(given)]})
    ov = [v for v in runs.video_versions("20261001-000000-07", "03") if v["role"] == "original"]
    check("a video's originals come from the run's own records: the clip its request names as the inventory holds it, and the source as the request sent it",
          [v["label"] for v in ov] == ["original · source-clip.mp4 (V05, the source clip as given)", "original as sent with a1 · source.mp4"], str([v["label"] for v in ov]))
    orig = ov[0]["path"] if ov else str(given / "source-clip.mp4")
    d = fill(orig)
    check("the original fills no prompt and says it was not generated", d.get("prompt") == "" and "original video" in d.get("note", ""), str(d)[:200])
    mkvideo(atp_run / "60-outputs" / "video-04" / "a1" / "raw.mp4", "0x503010", "yellow")
    (atp_run / "50-requests" / "video-04-request.json").write_text(json.dumps({"params": {"model": "wan3_0_prime", "duration": 5, "prompt": "Video four: the can on the shelf.",
                                                                                         "medias": [{"role": "image_references", "value": "m1"}]}, "medias_meaning": {"Image 2": "The shelf can: A14"}}))
    four_take = str(atp_run / "60-outputs" / "video-04" / "a1" / "raw.mp4")
    d = fill(four_take)
    check("a take of another video of the run fills its own video's prompt, never this video's", d.get("prompt", "").startswith("Video four") and d.get("request") == "video-04-request.json", str(d)[:200])
    s, d, _ = req("GET", "/api/product-sample?run=20261001-000000-07&video=03&wrong=" + quote(four_take))
    check("and its product sample is read from its own video's request", s == 200 and [x["label"] for x in d["samples"]] == ["The shelf can"], str(d)[:200])
    five_take = str(atp_run / "60-outputs" / "video-05" / "a1" / "raw.mp4")
    mkvideo(Path(five_take), "0x305020", "white")
    (atp_run / "50-requests" / "video-05-request.json").write_text(json.dumps({"params": {"model": "wan3_0_prime", "duration": 5, "prompt": "Video five.", "medias": [
        {"role": "image_references", "value": "m8", "meaning": "Image 1: C04 identity (A28 crop): facial identity only"},
        {"role": "image_references", "value": "m1", "meaning": "Image 2: the sealed test can (A14 front, tab flat), padded: structure, label"}]}}))
    s, d, _ = req("GET", "/api/product-sample?run=20261001-000000-07&video=05&wrong=" + quote(five_take))
    check("a request that writes the meaning on each input of its media list (the newer form) gives its product sample too, in the request's own words",
          s == 200 and [(x["asset_id"], x["label"]) for x in d["samples"]] == [("A14", "the sealed test can")], str(d)[:300])
    reg = runs._registry_products({"assets": {"product": {"P1": {"name": "Slim can", "canonical_geometry": "A06", "variants": {
        "P1-CC": {"flavour": "Cola", "label": "A14", "views": ["A15", "A04:Cola#2E221D"]}, "P1-OR": {"flavour": "Orange", "unused": "not this run", "label": "A35"}}}}}})
    check("a registry by product with its variants is read: each variant with its flavour and label image, an unused one left out",
          reg.get("P1-CC", {}).get("canonical") == "A14" and reg["P1-CC"]["ids"] == ["A14", "A15"] and reg["P1-CC"]["name"] == "Slim can · Cola"
          and reg.get("P1", {}).get("ids") == ["A06"] and "P1-OR" not in reg, str(reg))
    reg2 = runs._registry_products({"assets": {"products": {"P01": {"name": "Slim can", "geometry": ["A07"]}, "P01-WM": {"flavour": "Watermelon", "label": "A45", "views": ["A47"], "contents": ["A04"]}}}})
    check("a flat registry whose variants carry a label and views is read too, each variant named with its product (its contents images left out)",
          reg2.get("P01-WM") == {"name": "Slim can · Watermelon", "canonical": "A45", "ids": ["A45", "A47"]} and reg2.get("P01", {}).get("ids") == ["A07"], str(reg2))
    six_take = str(atp_run / "60-outputs" / "video-06" / "a1" / "raw.mp4")
    mkvideo(Path(six_take), "0x302050", "white")
    (atp_run / "50-requests" / "video-06-request.json").write_text(json.dumps({"params": {"model": "wan3_0_prime", "duration": 5, "prompt": "Video six.", "medias": [
        {"role": "video_references", "value": "v1"}, {"role": "image_references", "value": "m8"}, {"role": "image_references", "value": "m1"}]}}))
    (atp_run / "50-requests" / "video-06-uploads.json").write_text(json.dumps({"video": "06", "schema": 1, "uploads": {
        "a1 Image 1: C04 head": {"media_id": "m8", "file": str(atp_run / "55-references" / "video-06" / "a1" / "image1-character-C04-head.jpg"), "role": "image_references"},
        "a1 Image 2: P1 front padded": {"media_id": "m1", "file": str(atp_run / "55-references" / "video-06" / "a1" / "image2-product-P1-front-padded.png"), "role": "image_references"}}}))
    s, d, _ = req("GET", "/api/product-sample?run=20261001-000000-07&video=06&wrong=" + quote(six_take))
    check("a request that describes none of its inputs still gives its product sample through the video's uploads record (the product code in its key and file)",
          s == 200 and [x["asset_id"] for x in d["samples"]] == ["A14"], str(d)[:300])
    print("\n[the product sample picked from every image the generation used; a report edited where it stands]")
    for fn, col in (("image1-character-C04-head.jpg", (200, 150, 120)), ("image2-product-P1-front-padded.png", (0, 0, 255))):
        fp = atp_run / "55-references" / "video-06" / "a1" / fn
        fp.parent.mkdir(parents=True, exist_ok=True)
        Image.new("RGB", (300, 400), col).save(fp)
    s, d, _ = req("GET", "/api/generation-assets?run=20261001-000000-07&video=06&wrong=" + quote(six_take))
    ga = d.get("assets") or []
    names = {a["name"]: a for a in ga}
    check("every image the generation used is offered: the client's original (the automatic pick, ticked) and each file the uploads record names, by its own words",
          s == 200 and names.get("product-front.png", {}).get("suggested") is True and not names.get("image1-character-C04-head.jpg", {"suggested": True})["suggested"]
          and "image2-product-P1-front-padded.png" in names and names["image1-character-C04-head.jpg"]["label"] == "Image 1 · C04 head", str(ga)[:500])
    s, b, _ = req("GET", names["image1-character-C04-head.jpg"]["image_url"], raw=True) if "image1-character-C04-head.jpg" in names else (0, b"", None)
    check("each is served as a PNG thumbnail", s == 200 and b[:4] == b"\x89PNG")
    head = names.get("image1-character-C04-head.jpg", {}).get("path", "")
    s, d, _ = req("POST", "/api/misses", {"run": "20261001-000000-07", "video": "06", "title": "The wrong product sample", "model": "wan3_0_prime",
                                          "wrong_path": six_take, "wrong_label": "a1 · raw.mp4", "sample_files": [head]})
    pm = d["miss"]["id"] if s == 200 else 0
    s, d, _ = req("POST", f"/api/misses/{pm}/moments", {"frames": [{"src": "wrong", "role": "wrong", "t": 1.0}], "details": "The sample shows the wrong image."})
    pmo = d["moment"]["moment_id"] if s == 200 else 0
    pmeta = json.loads((core.DATA / "samples" / str(pmo) / "meta.json").read_text(encoding="utf-8")) if pmo else {}
    check("the ticked image is the product sample of the saved image (the admin's choice, not the automatic pick)",
          [x["file"] for x in pmeta.get("product_samples", [])] == ["image1-character-C04-head.jpg"] and pmeta.get("product_sample_chosen") is True, str(pmeta)[:300])
    s, d, _ = req("PATCH", f"/api/misses/{pm}", {"sample_files": [str(TMP / "reference-folder" / "not-used-here.png")]})
    check("a file that is not one of the images that generation used is refused", s == 400 and "not one of the images" in str(d), f"{s} {str(d)[:200]}")
    s, d, _ = req("PATCH", f"/api/misses/{pm}", {"sample_files": [], "title": "The wrong product sample, edited where it stands"})
    pmeta = json.loads((core.DATA / "samples" / str(pmo) / "meta.json").read_text(encoding="utf-8")) if pmo else {}
    check("untouched again ([]) the automatic pick is back, and a title changed where it stands rebuilds the image with it",
          s == 200 and d["miss"]["sample_files"] == [] and [x["file"] for x in pmeta.get("product_samples", [])] == ["product-front.png"]
          and pmeta.get("title") == "The wrong product sample, edited where it stands", str(pmeta)[:300])
    req("DELETE", f"/api/misses/{pm}")
    print("\n[a take joined from several generations: the prompt of each generation a sample's frames come from, under the seconds it made]")

    def _qcs_digest_lines():
        from loupe_app import qc as _q
        return _q._digest().splitlines()

    def mkclip(path, secs):
        path.parent.mkdir(parents=True, exist_ok=True)
        cp = subprocess.run([media.find_tool("ffmpeg"), "-y", "-loglevel", "error", "-f", "lavfi", "-i", f"color=c=0x203040:s=360x640:r=30:d={secs}",
                             "-pix_fmt", "yuv420p", str(path)], capture_output=True)
        assert cp.returncode == 0, cp.stderr[-300:]
    v7 = atp_run / "60-outputs" / "video-07"
    mkclip(v7 / "c1" / "a1" / "raw.mp4", 3)
    mkclip(v7 / "c1" / "a1" / "trimmed.mp4", 2)
    mkclip(v7 / "c2" / "a1" / "raw.mp4", 4)
    mkclip(v7 / "c2" / "a1" / "trimmed.mp4", 3)
    mkclip(v7 / "clean.mp4", 5)
    for clip, text in (("c1", "PROMPT-C1 the can stands on the desk."), ("c2", "PROMPT-C2 the lights go off and the can stays.")):
        (atp_run / "50-requests" / f"video-07-{clip}-request.json").write_text(json.dumps({"video": "07", "clip": clip, "attempt": 1,
            "params": {"model": "wan3_0_prime", "duration": 3, "prompt": text, "medias": [{"role": "image_references", "value": "m1"}]}}), encoding="utf-8")
    clean7 = str(v7 / "clean.mp4")
    s, d, _ = req("GET", "/api/request-prompt?run=20261001-000000-07&video=07&path=" + quote(clean7))
    segs7 = d.get("segments") or []
    check("a take joined from two generations fills one prompt per generation, in clip order, each as long as its clip's trimmed file",
          s == 200 and [(x["clip"], x["t_from"], x["t_to"]) for x in segs7] == [("c1", 0.0, 2.0), ("c2", 2.0, 5.0)] and segs7[0]["prompt"].startswith("PROMPT-C1")
          and segs7[1]["prompt"].startswith("PROMPT-C2") and "generated second 2 to second 5 of this video (generation 2 of 2 · clip c2" in d.get("prompt", "")
          and "this file 5.000 s" in d.get("note", ""), str(d)[:400])
    s, d, _ = req("GET", "/api/request-prompt?run=20261001-000000-07&video=07&path=" + quote(str(v7 / "c2" / "a1" / "trimmed.mp4")))
    check("a take of one clip fills that clip's own prompt (c2's take is never given c1's)", s == 200 and d.get("prompt", "").startswith("PROMPT-C2") and d.get("clip") == "c2", str(d)[:300])
    s, d, _ = req("POST", "/api/misses", {"run": "20261001-000000-07", "video": "07", "title": "The can grows after the lights go off", "model": "wan3_0_prime",
                                          "wrong_path": clean7, "wrong_label": "clean.mp4", "wrong_segments": segs7, "wrong_prompt": ""})
    sm = d["miss"]["id"] if s == 200 else 0
    wp = d["miss"]["wrong_prompt"] if s == 200 else ""
    check("generations sent with no text: the field is written from them, each prompt under the seconds of Video A it is responsible for", s == 200 and len(d["miss"]["wrong_segments"]) == 2
          and wp.startswith("This is the prompt that generated second 0 to second 2 of Video A (generation 1 of 2 · clip c1 · video-07-c1-request.json).\nPROMPT-C1")
          and "generated second 2 to second 5 of Video A (generation 2 of 2" in wp and d["miss"]["wrong_segments"][0]["src"] == "wrong" and d["miss"]["prompts"] == "wrong", str(d)[:400])
    s, d, _ = req("PATCH", f"/api/misses/{sm}", {"wrong_prompt": "The field as the form wrote it.", "wrong_segments": segs7})
    check("the field's text is kept as the form wrote it, with the take's generations beside it", s == 200 and d["miss"]["wrong_prompt"] == "The field as the form wrote it."
          and len(d["miss"]["wrong_segments"]) == 2, str(d)[:300])
    s, d, _ = req("POST", f"/api/misses/{sm}/moments", {"frames": [{"src": "wrong", "role": "wrong", "t": 1.0}, {"src": "wrong", "role": "wrong", "t": 4.0}], "details": "Bigger after the cut."})
    smo = d["moment"]["moment_id"] if s == 200 else 0
    s, d, _ = req("GET", f"/api/qc/samples/S{smo}?prompts=1", key=kq)
    smp = d.get("sample") or {}
    ws = smp.get("wrong_segments") or []
    pt = smp.get("prompts_text", "")
    check("the sample served with its prompts says which frames each prompt made", s == 200 and len(ws) == 2 and [f["n"] for f in ws[0]["frames"]] == [1] and [f["n"] for f in ws[1]["frames"]] == [2]
          and [(x["gen"], x["of"]) for x in ws] == [(1, 2), (2, 2)], str(ws)[:300])
    check("the judge's text: the prompt responsible for Frame 1 (second 0 to 2), then the one responsible for Frame 2 (second 2 to 5), the frames named as the image tags them",
          "This is the prompt responsible for Frame 1 (WRONG, 0:01.00) of this sample: it generated second 0 to second 2 of Video A (generation 1 of 2" in pt
          and "This is the prompt responsible for Frame 2 (WRONG, 0:04.00) of this sample: it generated second 2 to second 5 of Video A (generation 2 of 2" in pt
          and pt.index("PROMPT-C1") < pt.index("PROMPT-C2"), pt[:600])
    meta7 = json.loads((core.DATA / "samples" / str(smo) / "meta.json").read_text(encoding="utf-8"))
    check("each frame of the image is numbered, names its video and the generation that made it (meta), as the prompts name it",
          [(f["n"], f["video"], (f.get("generation") or {}).get("gen"), (f.get("generation") or {}).get("clip")) for f in meta7["frames"]] == [(1, "A", 1, "c1"), (2, "A", 2, "c2")]
          and meta7["frames"][1]["generation"]["t_from"] == 2.0, str(meta7["frames"])[:300])
    im7 = Image.open(core.DATA / "samples" / str(smo) / "composite.png").convert("RGB")
    check("each frame's tag has a second line under FRAME n (its video, source and generation, on a dark ground)", im7.getpixel((27, 55)) == (255, 45, 45)
          and im7.getpixel((27, 85)) == (46, 50, 60), f"{im7.getpixel((27, 55))} {im7.getpixel((27, 85))}")
    dg7 = [ln for ln in _qcs_digest_lines() if ln.startswith(f"S{smo} ")]
    check("the keeper's digest names each frame as its tag does: number, role, time, video, source and generation",
          dg7 and "Frame 1 WRONG 0:01.00, Video A (as generated), generation 1 of 2 · clip c1 (0–2 s)" in dg7[0]
          and "Frame 2 WRONG 0:04.00, Video A (as generated), generation 2 of 2 · clip c2 (2–5 s)" in dg7[0], (dg7 or [""])[0][:500])
    from loupe_app import qc as _qcs
    got = _qcs._fulfil({"kind": "sample_origin", "id": f"S{smo}"}, {}, TMP / "fulfil", False)
    check("sample_origin hands the judge the same text", got.get("ok") and "it generated second 2 to second 5 of Video A" in got.get("text", "") and "Frame 2 (WRONG, 0:04.00)" in got.get("text", ""), str(got)[:300])
    s, d, _ = req("POST", f"/api/misses/{sm}/moments", {"frames": [{"src": "wrong", "role": "wrong", "t": 3.0}, {"src": "wrong", "role": "wrong", "t": 4.5},
                                                                  {"src": "wrong", "role": "right", "t": 0.5}], "details": "Both misses after the cut."})
    smo2 = d["moment"]["moment_id"] if s == 200 else 0
    pp2 = _qcs.misses.prompts_of(smo2) or {}
    check("a sample whose WRONG frames all come from generation 2 gives the judge that generation's prompt alone", [x["gen"] for x in pp2.get("wrong_segments") or []] == [2]
          and "PROMPT-C2" in pp2.get("text", "") and "PROMPT-C1" not in pp2.get("text", "") and "the prompt of each generation this sample's frames come from" in pp2.get("text", ""), pp2.get("text", "")[:500])
    s, d, _ = req("PATCH", f"/api/misses/{sm}", {"fixed_prompt": "", "fixed_segments": [dict(x, src="wrong") for x in segs7]})
    pp3 = _qcs.misses.prompts_of(smo2) or {}
    check("a RIGHT frame taken from Video A: the corrected take's side gives Video A's generation that made it", [x["gen"] for x in pp3.get("fixed_segments") or []] == [1]
          and "of Video A (generation 1 of 2" in pp3.get("text", "").split("the corrected take")[-1] and "Frame 3 (RIGHT, 0:00.50)" in pp3.get("text", ""), pp3.get("text", "")[-500:])
    check("the keeper's digest says the flawed take was made of two generations", "the flawed take was made of 2 generations" in _qcs._digest())
    s, d, _ = req("GET", "/api/generation-assets?run=20261001-000000-07&video=07&wrong=" + quote(clean7))
    check("the product sample's images come from both generations' requests", s == 200 and {a["request"] for a in d.get("assets", [])} >= {"video-07-c1-request.json"}
          or {a["request"] for a in d.get("assets", [])} >= {"video-07-c2-request.json"}, str(d)[:300])
    s, d, _ = req("PATCH", f"/api/misses/{sm}", {"wrong_prompt": "One prompt typed by the admin."})
    check("a single prompt typed over the generations' prompts replaces them", s == 200 and d["miss"]["wrong_segments"] == [] and d["miss"]["wrong_prompt"] == "One prompt typed by the admin.", str(d)[:200])
    req("DELETE", f"/api/misses/{sm}")

    print("\n[Example frames only: the prompts are Example prompts, never the wrong or the corrected one]")
    c2take = str(v7 / "c2" / "a1" / "trimmed.mp4")
    s, d, _ = req("POST", "/api/misses", {"run": "20261001-000000-07", "video": "07", "title": "Two takes kept as examples", "model": "wan3_0_prime",
                                          "wrong_path": clean7, "fixed_path": c2take, "wrong_prompt": "EX-PROMPT-A the can on the desk.",
                                          "fixed_prompt": "EX-PROMPT-B the can in the hand."})
    em = d["miss"]["id"] if s == 200 else 0

    def emoment(frames):
        st, dd, _ = req("POST", f"/api/misses/{em}/moments", {"frames": frames, "details": "kept to remember"})
        return dd["moment"] if st == 200 else {}
    ex1 = emoment([{"src": "wrong", "role": "example", "t": 1.0}, {"src": "fixed", "role": "example", "t": 0.5}])
    check("a sample of Example frames only has Example prompts on both sides", ex1.get("prompt_roles") == {"wrong": "example", "fixed": "example"}, str(ex1.get("prompt_roles")))

    def db_roles():
        con = core.db()
        try:
            r = con.execute("SELECT wrong_prompt_role, fixed_prompt_role FROM misses WHERE id=?", (em,)).fetchone()
            return (r["wrong_prompt_role"], r["fixed_prompt_role"]) if r else None
        finally:
            con.close()
    s, d, _ = req("GET", f"/api/misses/{em}")
    check("the database keeps that the report's prompts are just examples, and the report says so", db_roles() == ("example", "example")
          and d["miss"]["prompt_roles"] == {"wrong": "example", "fixed": "example"}, f"{db_roles()} {d['miss'].get('prompt_roles')}")
    s, d, _ = req("GET", f"/api/qc/samples/{ex1.get('id')}?prompts=1", key=kq)
    et = (d.get("sample") or {}).get("prompts_text", "")
    check("the judge reads them as example takes, never as the wrong or the corrected take",
          s == 200 and (d.get("sample") or {}).get("prompt_roles") == {"wrong": "example", "fixed": "example"} and "this sample's frames are all Examples" in et
          and "the prompt of the example take of Video A" in et and "the prompt of the example take of Video B" in et and "EX-PROMPT-A" in et
          and "the prompt of the wrong take" not in et and "the prompt of the corrected take" not in et, et[:500])
    ex2 = emoment([{"src": "wrong", "role": "example", "t": 1.0}, {"src": "wrong", "role": "right", "t": 1.5}])
    ex3 = emoment([{"src": "wrong", "role": "wrong", "t": 1.0}, {"src": "fixed", "role": "right", "t": 0.5}])
    check("an Example frame of Video A beside a Right frame: Video A's prompt is an Example's, Video B's the corrected one; a Wrong frame keeps the wrong take",
          ex2.get("prompt_roles") == {"wrong": "example", "fixed": "correct"} and ex3.get("prompt_roles") == {"wrong": "flawed", "fixed": "correct"},
          f"{ex2.get('prompt_roles')} {ex3.get('prompt_roles')}")
    dlines = {ln.split(" ", 1)[0]: ln for ln in _qcs_digest_lines() if ln.startswith("S")}
    check("the keeper's digest names Example prompts as such, and a pair as before",
          "PROMPTS OF BOTH EXAMPLE TAKES SAVED" in dlines.get(ex1.get("id"), "")
          and "PROMPTS SAVED: THE EXAMPLE TAKE OF VIDEO A (just an example) AND THE CORRECT TAKE (VIDEO B)" in dlines.get(ex2.get("id"), "")
          and "PROMPTS OF BOTH TAKES SAVED: VIDEO A'S GENERATED THE FLAWED VERSION, VIDEO B'S IS THE CORRECT TAKE" in dlines.get(ex3.get("id"), ""), dlines.get(ex2.get("id"), "")[-300:])
    check("with a sample whose frames are Wrong and Right, the database keeps Video A's prompt as the one that generated the flawed version and Video B's as the correct take's",
          db_roles() == ("flawed", "correct"), str(db_roles()))
    s3, d3, _ = req("GET", f"/api/qc/samples/{ex3.get('id')}?prompts=1", key=kq)
    t3 = (d3.get("sample") or {}).get("prompts_text", "")
    check("the judge reads Video A's prompt as the one that generated the flawed version and Video B's as the correct take's",
          "the prompt of the flawed take (Video A: its prompt generated the flawed version)" in t3 and "the prompt of the correct take (Video B)" in t3, t3[:400])
    from loupe_app import qc as _qce
    lib_ex = {x["id"]: x for x in _qce.library()}
    bl = _qce.sample_lines(lib_ex[ex1["id"]]) if ex1.get("id") in lib_ex else ""
    check("the brief says the prompts kept are example takes", "Video A's prompt, an example take" in bl and "Video B's prompt, an example take" in bl, bl[:600])
    req("DELETE", f"/api/moments/{ex2['moment_id']}")
    req("DELETE", f"/api/moments/{ex3['moment_id']}")
    check("deleting the other samples makes the report's prompts examples again in the database", db_roles() == ("example", "example"), str(db_roles()))
    req("DELETE", f"/api/misses/{em}")
    late = media.frame_file(Path(six_take), 999)
    check("a frame asked for past the end of a video is its last picture, never an error", late.is_file())
    s, d, _ = req("POST", "/api/misses", {"run": "20261001-000000-07", "video": "03", "title": "The pen became a can", "model": "wan3_0_prime",
                                          "wrong_path": wrong, "fixed_path": orig, "fixed_label": ov[0]["label"] if ov else ""})
    om = d["miss"]["id"]
    s, d, _ = req("POST", f"/api/misses/{om}/moments", {"frames": [{"src": "wrong", "role": "wrong", "t": 1.0}, {"src": "fixed", "role": "example", "t": 1.0}],
                                                         "shapes": [{"type": "circle", "frame": 0, "cx": .4, "cy": .45, "rx": .1, "ry": .06}], "details": "The pen became a can."})
    om_mo = d["moment"]
    om_dir = core.DATA / "samples" / str(om_mo["moment_id"])
    meta = json.loads((om_dir / "meta.json").read_text(encoding="utf-8"))
    check("a frame of the original is tagged 'original video' (never 'correct version') in the image's record and in what QC and the keeper read",
          s == 200 and [f["from"] for f in meta["frames"]] == ["as generated", "original video"] and [f["desc"] for f in om_mo["frames"]] == ["as generated", "original video"], str(meta["frames"]))
    oim = Image.open(om_dir / "composite.png").convert("RGB")
    check("only the frame with a circle has a zoom; the unmarked frame of the original has none", oim.getpixel((300, 1384)) != (245, 245, 247) and oim.getpixel((888, 1384)) == (245, 245, 247),
          f"{oim.getpixel((300, 1384))} {oim.getpixel((888, 1384))}")
    s, d, _ = req("POST", "/api/qc/brief", {"q": "the pen became a can", "model": "wan3_0_prime"}, key=kq)
    check("the brief names the original video as that frame's source", "(original video)" in d["text"], d["text"][:600])
    s, d, _ = req("PATCH", f"/api/misses/{om}", {"wrong_path": orig, "wrong_label": ov[0]["label"] if ov else "", "fixed_path": four_take, "fixed_label": "video 04 · a1 · raw.mp4"})
    s, d, _ = req("GET", f"/api/misses/{om}")
    fr2 = d["miss"]["moments"][0]["frames"] if s == 200 and d["miss"]["moments"] else []
    check("the original on side A and a take of video 04 on side B: the tags say 'original video' and 'correct version · video 04'",
          [f["desc"] for f in fr2] == ["original video", "correct version · video 04"], str(fr2))
    req("DELETE", f"/api/misses/{om}")
    print("\n[the keeper's readings of the pictures: one JSON per sample, only new or changed samples are read]")
    from loupe_app import qc as _qc
    if not _qc.library():
        req("PATCH", "/api/moments/1", {"status": "active"})
    calls = []
    real_extract, real_claude, real_run = _qc._extract_one, _qc._claude, _qc._run_claude
    _qc._claude = lambda: "claude"

    def fake_extract(s, model):
        calls.append(s["id"])
        return {"reading": {"marked": f"the marks of {s['id']}", "product_and_scene": "p", "frames_seen": [{"frame": "WRONG 0:01.00", "seen": "a can"}]},
                "cost": 0.01, "turns": 3, "images_read": True}
    _qc._extract_one = fake_extract
    try:
        ids = [s["id"] for s in _qc.library()]
        r1 = _qc.sync_understanding("test")
        check("the first pass reads every active sample once and keeps one JSON per sample", ids and sorted(r1["read"]) == sorted(ids) and len(calls) == len(ids)
              and all((core.DATA / "keeper" / "understood" / f"{i}.json").exists() for i in ids), str(r1))
        r2 = _qc.sync_understanding("test")
        check("when the readings match the library one for one, nothing is read again", r2["state"] == "current" and len(calls) == len(ids), str(r2))
        check("the keeper's digest carries each collection", "READ (collected from its picture, no verdict" in _qc._digest() and f"the marks of {ids[0]}" in _qc._digest())
        req("PATCH", f"/api/moments/{ids[0][1:]}", {"details": "Edited words, so the reading is stale."})
        r3 = _qc.sync_understanding("test")
        check("an edited sample is read again, and only it", r3["read"] == [ids[0]] and len(calls) == len(ids) + 1, str(r3))
        s, d, _ = req("GET", "/api/qc/keeper/understanding")
        check("the admin page gets every reading and the counts", s == 200 and d["status"]["current"] and d["status"]["understood"] == len(ids)
              and any(x["reading"] and x["reading"]["marked"] for x in d["readings"]), str(d)[:200])
        s, d, _ = req("GET", "/api/qc/keeper/state", key=kq)
        check("the qc key sees the reading counts, not the readings", s == 200 and d.get("understanding", {}).get("understood") == len(ids) and "readings" not in d, str(d)[:200])
        req("PATCH", f"/api/moments/{ids[0][1:]}", {"status": "archived"})
        r4 = _qc.sync_understanding("test")
        check("a sample that leaves the library has its reading retired, nothing is read", r4["retired"] == 1 and r4["read"] == []
              and not (core.DATA / "keeper" / "understood" / f"{ids[0]}.json").exists() and any((core.DATA / "keeper" / "understood" / "retired").glob(f"{ids[0]}-*.json")), str(r4))
        req("PATCH", f"/api/moments/{ids[0][1:]}", {"status": "active"})
        _qc.sync_understanding("test")

        print("\n[coupling: the answers carry the readings, what the keeper missed reaches the admin, a run start starts the readings]")
        sid0 = ids[0]

        def fake_keeper(prompt, *a, **k):
            if "PLANNING question" in prompt:
                return {"result": json.dumps({"hints": [{"id": sid0, "trigger": "the can is set down on the desk", "right_outcome": "the can stays the same size",
                                                         "lever": "restate its size at that beat", "caveat": "c", "confidence": "high"}]}), "total_cost_usd": 0}
            if "QC question" in prompt:
                return {"result": json.dumps({"relevant": [{"id": sid0, "why": "w", "check": "c", "wrong_pattern": "p"}], "advice": "a",
                                              "not_covered": "no sample shows a can held in a moving car",
                                              "needs": [{"kind": "ask_agent", "question": "is the can's tab up at 2 s?", "why": "the frame list stops at 1 s"}]}), "total_cost_usd": 0}
            return {"result": "ready", "total_cost_usd": 0}
        _qc._run_claude = fake_keeper
        _qc.keeper_reset("test")
        s, d, _ = req("POST", "/api/qc/consult", {"run": "r", "video": "03", "model": "wan3_0_prime", "question": "Which samples apply?"}, key=kq)
        rel = (d.get("relevant") or [{}])[0]
        check("a QC answer's relevant sample carries what the collector saw (short) and the path of the whole collection", s == 200 and rel.get("reading_path", "").endswith(f"{sid0}.json")
              and rel.get("reading", {}).get("marked"), str(d)[:300])
        check("every answer says how much of the library the keeper holds from the pictures", d.get("library_read", {}).get("understood") == len(ids) and d["library_read"]["not_read"] == [])
        check("what only the QC agent can answer comes back unmet", d.get("needs_unmet") and d["needs_unmet"][0]["kind"] == "ask_agent")
        s, d, _ = req("POST", "/api/qc/consult", {"purpose": "plan", "run": "r", "video": "04", "model": "wan3_0_prime", "question": "Beat 1: he sets the can down."}, key=kq)
        check("a planning hint carries the path of its sample's reading", s == 200 and (d.get("hints") or [{}])[0].get("reading_path", "").endswith(f"{sid0}.json"), str(d)[:300])
        s, d, _ = req("GET", "/api/qc/keeper")
        qa = next((c for c in d["consults"] if c["purpose"] == "qc"), {})
        check("the consult log keeps what the keeper asked for, could not get and found not covered, whole, for the admin's page",
              qa.get("asks", {}).get("not_covered", "").startswith("no sample shows") and qa["asks"].get("needs_unmet") and qa["asks"]["needs_unmet"][0].get("question"), str(qa)[:300])
        print("\n[the judge writes the prompt template from the saved prompts it chooses; a QC question carries the prompt used, the template and the brief]")
        mid0 = next(x["miss_id"] for x in _qc.library() if x["id"] == sid0)
        before_pp = _qc.misses.prompts_of(int(sid0[1:])) or {}
        longw = "PROMPT-W the can is set down on the desk. " + "More of that beat. " * 300
        req("PATCH", f"/api/misses/{mid0}", {"wrong_prompt": longw, "fixed_prompt": "PROMPT-F the can is set down, as tall as the mug.", "prompt_note": "the mug"})
        seen = []

        def judge(prompt, *a, **k):
            seen.append(prompt)
            if "TEMPLATE REQUEST" in prompt:
                return {"result": json.dumps({"read": [sid0, "S999"]}), "total_cost_usd": 0}
            if "Step 2 of the template request" in prompt:
                return {"result": json.dumps({"template": "TEMPLATE-MARK Shot 1: the can stands on the desk, as tall as the mug. [SLOT: the product's upload tag]",
                                              "basis": [{"id": sid0, "worked": "the height against the mug", "failed": "no size stated", "where": "shot 1"}, {"id": "S999"}],
                                              "notes": "keep the mug line", "changes": [{"what": "beat 2's cut", "why": "w", "id": sid0}], "not_covered": ""}), "total_cost_usd": 0}
            if "rests on samples whose two prompts" in prompt:
                return {"result": json.dumps({"relevant": [{"id": sid0, "why": "judged after reading both prompts", "check": "c", "wrong_pattern": "p"}], "advice": "a",
                                              "cause": "shot 1 gave no size", "template_fix": "the can as tall as the mug"}), "total_cost_usd": 0}
            if "QC question" in prompt:
                return {"result": json.dumps({"relevant": [{"id": sid0, "why": "first answer", "check": "c", "wrong_pattern": "p"}], "advice": "a",
                                              "cause": "first cause", "template_fix": "first fix"}), "total_cost_usd": 0}
            return {"result": "ready", "total_cost_usd": 0}
        _qc._run_claude = judge
        s, d, _ = req("POST", "/api/qc/consult", {"purpose": "template", "run": "r", "video": "05", "model": "wan3_0_prime",
                                                  "question": "BRIEF-MARK video 05: he sets the can on the desk; the goal is the can's true size.",
                                                  "structure": "STRUCTURE-MARK Shot 1: ... Shot 2: ...", "video_model": {"id": "wan3_0_prime", "name": "MODEL-MARK"}}, key=kq)
        step2 = next((p for p in seen if "Step 2 of the template request" in p), "")
        check("a template request: the judge is briefed with the agent's brief, the video model and its structure, then given both prompts it chose, whole",
              s == 200 and any("TEMPLATE REQUEST" in p and "BRIEF-MARK" in p and "STRUCTURE-MARK" in p and "MODEL-MARK" in p for p in seen) and longw.strip() in step2
              and "PROMPT-F" in step2 and "Not sent: S999" in step2, str(d)[:300])
        check("the answer is the judge's template with its basis (only samples that exist), notes and changes, and the samples it read; none is sent twice",
              d.get("purpose") == "template" and d.get("template", "").startswith("TEMPLATE-MARK") and [x["id"] for x in d.get("basis", [])] == [sid0]
              and d.get("read") == [sid0] and d.get("changes") and d.get("notes") == "keep the mug line" and not any("rests on samples" in p for p in seen), str(d)[:300])
        s, d, _ = req("GET", "/api/qc/keeper")
        check("the consult log keeps the template request under its own purpose", s == 200 and any(c["purpose"] == "template" for c in d["consults"]))
        seen.clear()
        s, d, _ = req("POST", "/api/qc/consult", {"run": "r", "video": "05", "model": "wan3_0_prime", "question": "the can is smaller than the mug at 2 s",
                                                  "request_prompt": "USED-PROMPT the can on the desk.", "template": "TEMPLATE-MARK", "brief": "BRIEF-MARK",
                                                  "request_params": {"model": "wan3_0_prime", "duration": "5"}, "video_model": {"id": "wan3_0_prime", "name": "MODEL-MARK"}}, key=kq)
        q1 = next((p for p in seen if "QC question" in p), "")
        rp = next((p for p in seen if "rests on samples" in p), "")
        check("a QC question carries the prompt used, its video model and parameters, the template given and the brief", "USED-PROMPT" in q1 and "TEMPLATE-MARK" in q1
              and "BRIEF-MARK" in q1 and "MODEL-MARK" in q1 and '"duration": "5"' in q1, q1[:300])
        check("before the QC answer is final the judge gets both prompts of the sample it cites, whole, and its second answer is kept",
              longw.strip() in rp and d.get("prompts_judged") == [sid0] and d["relevant"][0]["why"] == "judged after reading both prompts", str(d)[:300])
        check("the QC answer carries the judge's cause and the part rewritten", d.get("cause") == "shot 1 gave no size" and d.get("template_fix") == "the can as tall as the mug", str(d)[:300])
        seen.clear()

        def judge_origin(prompt, *a, **k):
            seen.append(prompt)
            if "QC question" in prompt:
                return {"result": json.dumps({"relevant": [], "needs": [{"kind": "sample_origin", "id": sid0, "why": "how was it prompted"}]}), "total_cost_usd": 0}
            if "Here is what you asked for" in prompt:
                return {"result": json.dumps({"relevant": [{"id": sid0, "why": "w", "check": "c", "wrong_pattern": "p"}], "advice": "a"}), "total_cost_usd": 0}
            return {"result": "not json", "total_cost_usd": 0}
        _qc._run_claude = judge_origin
        s, d, _ = req("POST", "/api/qc/consult", {"run": "r", "video": "05", "question": "same"}, key=kq)
        check("a sample whose prompts the judge already fetched in that consult is not sent again", s == 200 and not any("rests on samples" in p for p in seen)
              and not d.get("prompts_judged") and d.get("relevant"), str(d)[:200])

        def judge_bad(prompt, *a, **k):
            if "rests on samples" in prompt:
                return {"result": "not json", "total_cost_usd": 0}
            return judge(prompt)
        _qc._run_claude = judge_bad
        s, d, _ = req("POST", "/api/qc/consult", {"run": "r", "video": "05", "question": "again"}, key=kq)
        check("a second answer that does not parse keeps the first one", s == 200 and d["relevant"][0]["why"] == "first answer" and d.get("prompts_judged") == [sid0], str(d)[:200])
        req("PATCH", f"/api/misses/{mid0}", {k: before_pp.get(k) or "" for k in ("wrong_prompt", "fixed_prompt", "prompt_note")})
        _qc._run_claude = fake_keeper
        req("PATCH", f"/api/moments/{sid0[1:]}", {"details": "Edited again, so its reading is stale."})
        n_before = len(calls)
        s, d, _ = req("POST", "/api/qc/keeper/sync", {}, key=kq)
        check("a run start's keeper sync starts reading the samples without a current reading and says so", s == 200 and d.get("reading_started") is True
              and sid0 in (d.get("understanding", {}).get("stale") or []), str(d)[:300])
        import time as _t
        for _ in range(50):
            if len(calls) > n_before:
                break
            _t.sleep(0.1)
        check("the background reading reads only that sample", calls[n_before:] == [sid0], str(calls[n_before:]))

        print("\n[a failing reading is given up after two tries of the same content; a sample that comes back gets its reading back; the reader model is a setting]")

        def failing_extract(s, model):
            calls.append("fail-" + s["id"])
            raise RuntimeError("the reading was not a JSON object with the miss in it")
        _qc._extract_one = failing_extract
        req("PATCH", f"/api/moments/{sid0[1:]}", {"details": "Changed, so it is read again, and the reading fails."})
        _qc.sync_understanding("test")
        _qc.sync_understanding("test")
        n_fail = calls.count("fail-" + sid0)
        r7 = _qc.sync_understanding("test")
        st7 = _qc.understanding_status()
        check("a sample whose reading failed twice, unchanged, is not read again on its own: it is listed as failed and the readings count as current (no call per tick)",
              n_fail == 2 and calls.count("fail-" + sid0) == 2 and r7["state"] == "current" and sid0 in r7.get("gave_up", []) and sid0 in st7["failed"] and st7["current"], f"{n_fail} {r7} {st7}")
        check("the keeper's digest says the sample could not be read", "COULD NOT BE READ" in _qc._digest())
        _qc.sync_understanding("test", [sid0])
        check("the admin's request reads it once more", calls.count("fail-" + sid0) == 3)
        _qc._extract_one = fake_extract
        req("PATCH", f"/api/moments/{sid0[1:]}", {"details": "Changed again: a changed sample starts again."})
        r8 = _qc.sync_understanding("test")
        check("a changed sample is read again after failures, and a success clears its failure record",
              r8["read"] == [sid0] and not (core.DATA / "keeper" / "understood" / "failed" / f"{sid0}.json").exists(), str(r8))
        n_calls = len(calls)
        req("PATCH", f"/api/moments/{sid0[1:]}", {"status": "archived"})
        _qc.sync_understanding("test")
        req("PATCH", f"/api/moments/{sid0[1:]}", {"status": "active"})
        r9 = _qc.sync_understanding("test")
        check("a sample that comes back unchanged gets its earlier reading back, with no new reading", r9["restored"] == [sid0] and r9["read"] == [] and len(calls) == n_calls, str(r9))
        s, d, _ = req("POST", "/api/settings", {"keeper_extract_model": "claude-opus-5-5"})
        check("the reader model is a setting the admin changes", s == 200 and d.get("keeper_extract_model") == "claude-opus-5-5" and _qc.understanding_status()["model"] == "claude-opus-5-5", str(d)[:200])
        req("POST", "/api/settings", {"keeper_extract_model": _qc.EXTRACT_MODEL_DEFAULT})
        s, d, _ = req("POST", "/api/settings", {"keeper_effort": "medium"})
        check("the keeper's effort is refused below high", s == 400 and _qc.effort() == "high", f"{s} {str(d)[:200]}")
        s, d, _ = req("POST", "/api/settings", {"keeper_effort": "max"})
        c = _qc._claude_cmd("claude", "claude-haiku-5-5", "sid", False)
        check("the admin raises the effort, and every keeper and reader call carries it", s == 200 and d.get("keeper_effort") == "max"
              and c[c.index("--effort") + 1] == "max" and c[-2:] == ["--session-id", "sid"] and req("GET", "/api/qc/keeper")[1]["keeper"]["effort"] == "max", str(d)[:200])
        core.setting_set("keeper_effort", "low")
        c = _qc._claude_cmd("claude", "claude-haiku-5-5", "sid", True)
        check("an effort below high that was stored around the API still runs at high", _qc.effort() == "high" and c[c.index("--effort") + 1] == "high"
              and c[-2:] == ["--resume", "sid"])
        req("POST", "/api/settings", {"keeper_effort": "high"})
        _qc._extract_one = real_extract
        seq = iter([{"result": json.dumps({"marked": "x"}), "num_turns": 1, "total_cost_usd": 0.01}, {"result": json.dumps({"marked": "seen", "frames_seen": []}), "num_turns": 3, "total_cost_usd": 0.02}])
        prompts = []
        _qc._run_claude = lambda prompt, *a, **k: (prompts.append(prompt), next(seq))[1]
        one = _qc._extract_one(_qc.library()[0], "m")
        check("a collection given without opening the picture is asked once more and kept only when the picture was opened", one["images_read"] and one["reading"]["marked"] == "seen"
              and len(prompts) == 2 and "Read the composite" in prompts[0] and "composite.png" in prompts[0], str(one))
    finally:
        _qc._extract_one, _qc._claude, _qc._run_claude = real_extract, real_claude, real_run
    if with_keeper:
        print("\n[keeper] (a real headless session)")
        s, d, _ = req("POST", "/api/qc/consult", {"run": "r", "video": "03", "model": "wan3_0_prime", "question": "The product changes colour after a cut. Which samples apply?"}, key=kq)
        check("the keeper answers from its library", s == 200 and d.get("mode") == "keeper" and any(r["id"] == "S1" for r in d.get("relevant", [])), str(d)[:400])
        s, d, _ = req("POST", "/api/qc/consult", {"run": "r", "video": "03", "question": "Same question again, brief."}, key=kq)
        check("a second consult resumes the same session", s == 200 and d.get("mode") == "keeper")
        s, d, _ = req("POST", "/api/qc/consult", {"purpose": "plan", "run": "r", "video": "04", "model": "wan3_0_prime", "question": "Beat 1: a red pack on a desk, hard cut to a close-up of the same pack.\nBeat 2: he picks it up."}, key=kq)
        check("a planning question gets text-only hints with real ids (or none)", s == 200 and d.get("mode") == "keeper" and d.get("purpose") == "plan"
              and all(h["id"] == "S1" and "composite_path" not in h and h["confidence"] in ("high", "medium") for h in d.get("hints", [])), str(d)[:400])
        s, d, _ = req("GET", "/api/qc/keeper")
        check("the keeper session is recorded and read-only", d["keeper"].get("session_id") and d["keeper"]["turns"] >= 3)
        check("the consult log says what each consult was for", {c["purpose"] for c in d["consults"]} >= {"qc", "plan"})
    print("\n[one Loupe per port: a second server never shares a port, a test's data folder never starts one on the shared port]")
    first = server.OnePortServer((core.HOST, 0), server.Handler)
    try:
        server.OnePortServer((core.HOST, first.server_port), server.Handler).server_close()
        took = True
    except OSError:
        took = False
    first.server_close()
    check("a second server cannot take a port another one listens on", not took)
    import loupe_client as _lcm
    popens = []
    import types as _types
    real = (_lcm.DATA, _lcm.health, _lcm.subprocess, os.environ.pop("LOUPE_PORT", None))
    _lcm.health = lambda timeout=1.2: None
    _lcm.subprocess = _types.SimpleNamespace(Popen=lambda cmd, **k: popens.append(cmd), DEVNULL=real[2].DEVNULL)      # only the client's own reference: the server's threads keep the real one
    try:
        _lcm.DATA = TMP / "other-data"
        (TMP / "other-data").mkdir(exist_ok=True)
        r1 = _lcm.ensure(wait=0.1)
        (TMP / "other-data" / "server.json").write_text(json.dumps({"port": 8787}))
        r2 = _lcm.ensure(wait=0.1)
        check("a data folder of its own never starts a server on the shared port 8787 (no port of its own, or 8787 in its server.json)", r1 is False and r2 is False and not popens, str(popens))
        (TMP / "other-data" / "server.json").write_text(json.dumps({"port": 51999}))
        _lcm.ensure(wait=0.1)
        check("with a port of its own it starts one there", popens and popens[-1][-2:] == ["--port", "51999"], str(popens))
    finally:
        _lcm.DATA, _lcm.health, _lcm.subprocess = real[:3]
        if real[3] is not None:
            os.environ["LOUPE_PORT"] = real[3]
    print("\n[cleanup]")
    httpd.shutdown()
    shutil.rmtree(TMP, ignore_errors=True)
    print(f"\n{len(OK)} passed, {len(BAD)} failed")
    if BAD:
        print("FAILED:", ", ".join(BAD))
    sys.exit(1 if BAD else 0)


if __name__ == "__main__":
    main("--keeper" in sys.argv)
