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
    check("hint text is shortened and carries no image path", all(len(h["trigger"]) <= 220 and "composite_path" not in h for h in hints))
    ok_h = {"id": "S1", "trigger": "t", "right_outcome": "the count reads three after the reveal", "lever": "restate the count once at that beat", "caveat": "c", "confidence": "high"}
    bad = [dict(ok_h, lever="Never lay the pack on the floor"), dict(ok_h, right_outcome="always keep it on the desk"), dict(ok_h, trigger="avoid the desk"), dict(ok_h, lever="the pack must stand")]
    hints, held = _qc.plan_hints_from({"hints": bad + [ok_h]}, {"model": "wan3_0_prime"}, lib)
    check("a hint worded as a universal command is held back, a condition is kept", [h["id"] for h in hints] == ["S1"] and held == 4, f"{[h['lever'] for h in hints]} held {held}")
    s2, d2, _ = req("POST", "/api/qc/consult", {"purpose": "plan", "model": "wan3_0_prime", "product_details": "flat sachet, 6 x 9 cm, tear notch at the top", "question": "Beat 1: he sets the sachet down."}, key=kq)
    check("a consult carries the product's details without trouble", s2 == 200)
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
    check("four frames: three in a row with their zoom row, and the fourth alone in its row beside its close range", s == 200 and four.getpixel((319, 1659)) == (59, 110, 220) and four.getpixel((903, 1659)) == (70, 74, 90) and four.getpixel((27 + 584 * 2, 55)) == (255, 45, 45), f"{four.size} {four.getpixel((319, 1659))} {four.getpixel((903, 1659))}")
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
    print("\n[cleanup]")
    httpd.shutdown()
    shutil.rmtree(TMP, ignore_errors=True)
    print(f"\n{len(OK)} passed, {len(BAD)} failed")
    if BAD:
        print("FAILED:", ", ".join(BAD))
    sys.exit(1 if BAD else 0)


if __name__ == "__main__":
    main("--keeper" in sys.argv)
