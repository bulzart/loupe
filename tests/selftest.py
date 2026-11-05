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
    _I.new("RGBA", (400, 1000), (0, 0, 255, 255)).save(run / "30-assets" / "stage" / "pack.png")      # the file the request sent under m1
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
                         "history": [{"at": "2026-10-14T10:00:00+02:00", "to": "submitted"}, {"at": "2026-10-14T10:00:16+02:00", "to": "failed"}]},
                        {"video": "03", "lane": "03", "attempt": 1, "fingerprint": runs.request_fingerprint(req), "status": "completed", "job_id": "job-112",
                         "request_file": str(run / "50-requests" / "video-03-request.json")}]}))      # sent again, and it made the take a1
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
    # the admin, 2026-11-02: a frame with a zoom stays clean in the composite, its marks are on the zoom only (the full-size annotated file above keeps them)
    from PIL import ImageChops as _IC
    _w1 = Image.open(core.DATA / "samples" / "1" / "wrong.png").convert("RGB").resize((560, 996), Image.LANCZOS)
    _zf = Image.open(core.DATA / "samples" / "1" / "zoom.png").convert("RGB")
    check("the marked frame stays clean in the composite: its marks are on its zoom only",
          _IC.difference(im.convert("RGB").crop((24, 104, 584, 1100)), _w1).getbbox() is None
          and any(p[0] > 200 and p[1] < 100 and p[2] < 100 for p in _zf.crop((24, 0, 584, 520)).getdata()))
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
    print("\n[the prompts of the flawed and the correct version: in the record, never in the image, never diffed]")
    req("PATCH", "/api/moments/1", {"status": "active"})       # S1 was archived above; it is active for this block and archived again after it
    img1, meta1 = core.DATA / "samples" / "1" / "composite.png", core.DATA / "samples" / "1" / "meta.json"
    before_img, before_meta = img1.read_bytes(), meta1.read_text(encoding="utf-8")
    s, d, _ = req("PATCH", "/api/misses/1", {"wrong_prompt": "PROMPT-A the can on the desk.", "fixed_prompt": "PROMPT-B the can on the desk, as tall as the mug.", "prompt_note": "the height was stated against the mug"})
    check("the prompts of both boxes (the flawed version's, the correct version's) and the admin's note are kept on the report", s == 200 and d["miss"]["wrong_prompt"].startswith("PROMPT-A") and d["miss"]["fixed_prompt"].startswith("PROMPT-B") and d["miss"]["prompts"] == "pair", str(d)[:200])
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
    check("the brief says which prompts are kept, named by what they are, and where to read them, without their text",
          "kept in the record: the flawed version's prompt (the generations the frames called WRONG come from) and the correct version's prompt" in d["text"]
          and "?prompts=1" in d["text"] and "PROMPT-A" not in d["text"], d["text"][:300])
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
    check("clearing one prompt leaves the other (the flawed version's prompt only)", s == 200 and d["miss"]["prompts"] == "wrong")
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
    check("the seed carries the prompts of both boxes with the misses", str(seeded_prompt or "").startswith("PROMPT-A"))
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
    check("QC sees it as a scenario with the three texts", "SCENARIO" in d["text"] and "the AI agents missed or could not understand: The agents read the hold" in d["text"] and "idea (what was meant to happen)" in d["text"], d["text"][:400])
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
                                                                         {"src": "wrong", "role": "wrong", "t": 2.5}],
                                                              "shapes": [{"type": "rect", "frame": 0, "x1": .2, "y1": .3, "x2": .6, "y2": .5}]})
    four = Image.open(core.DATA / "samples" / str(d["moment"]["moment_id"]) / "composite.png").convert("RGB")
    check("four frames: three in a row with a zoom under the marked one only, and the fourth (unmarked) alone in its row with no close range", s == 200
          and four.getpixel((612, 1689)) == (255, 45, 45) and four.getpixel((1400, 1960)) == (245, 245, 247) and four.getpixel((27 + 584 * 2, 55)) == (255, 45, 45)
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
    def ledger_add(req_name: str, job: str):
        """A completed submission of that request in the fake run's ledger, with the fingerprint the pipelines record."""
        stp = atp_run / "state.json"
        st_ = json.loads(stp.read_text(encoding="utf-8"))
        rq_ = json.loads((atp_run / "50-requests" / req_name).read_text(encoding="utf-8"))
        nn_ = req_name.split("-")[1]
        st_.setdefault("submissions", []).append({"video": nn_, "lane": nn_, "attempt": 1, "status": "completed", "job_id": job,
                                                  "request_file": str(atp_run / "50-requests" / req_name), "fingerprint": runs.request_fingerprint(rq_)})
        stp.write_text(json.dumps(st_), encoding="utf-8")
    six_take = str(atp_run / "60-outputs" / "video-06" / "a1" / "raw.mp4")
    mkvideo(Path(six_take), "0x302050", "white")
    (atp_run / "50-requests" / "video-06-request.json").write_text(json.dumps({"params": {"model": "wan3_0_prime", "duration": 5, "prompt": "Video six.", "medias": [
        {"role": "video_references", "value": "v1"}, {"role": "image_references", "value": "m8"}, {"role": "image_references", "value": "m1"}]}}))
    (atp_run / "50-requests" / "video-06-uploads.json").write_text(json.dumps({"video": "06", "schema": 1, "uploads": {
        "a1 Image 1: C04 head": {"media_id": "m8", "file": str(atp_run / "55-references" / "video-06" / "a1" / "image1-character-C04-head.jpg"), "role": "image_references"},
        "a1 Image 2: P1 front padded": {"media_id": "m1", "file": str(atp_run / "55-references" / "video-06" / "a1" / "image2-product-P1-front-padded.png"), "role": "image_references"}}}))
    ledger_add("video-06-request.json", "job-606")
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
    check("only the images sent with that generation are offered, each once and every one ticked: the files uploaded under the request's media ids "
          "(never the client's original), each with what it is and the job it was sent with",
          s == 200 and sorted(names) == ["image1-character-C04-head.jpg", "image2-product-P1-front-padded.png"] and all(a["suggested"] for a in ga)
          and names["image1-character-C04-head.jpg"]["kind"] == "character" and names["image2-product-P1-front-padded.png"]["kind"] == "product"
          and names["image1-character-C04-head.jpg"]["label"] == "Image 1 · image1-character-C04-head.jpg · its original file is not recorded"
          and names["image2-product-P1-front-padded.png"]["source"] == "sent with job job-606", str(ga)[:600])
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
    check("untouched again ([]) every image sent with the generation is back in the band, and a title changed where it stands rebuilds the image with it",
          s == 200 and d["miss"]["sample_files"] == [] and [x["file"] for x in pmeta.get("product_samples", [])] == ["image1-character-C04-head.jpg", "image2-product-P1-front-padded.png"]
          and len(pmeta.get("images_sent") or []) == 2 and pmeta.get("product_sample_chosen") is False
          and pmeta.get("title") == "The wrong product sample, edited where it stands", str(pmeta)[:300])
    req("DELETE", f"/api/misses/{pm}")
    print("\n[a take joined from several generations: the prompt of each generation a sample's frames come from, under the seconds it made]")

    def _qcs_digest_lines():
        from loupe_app import qc as _q
        return _q._digest().splitlines()

    def _qcs_records():
        from loupe_app import qc as _q
        return _q.records_of(_q._digest())

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
    ledger_add("video-07-c1-request.json", "job-701")
    ledger_add("video-07-c2-request.json", "job-702")
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
    check("the judge's text: each prompt named as the flawed version's, with the seconds of Video A it generated and the frame it made, generation 1 then 2",
          "This is a prompt of the flawed version (the generation the frames called WRONG come from): it generated second 0 to second 2 of Video A (generation 1 of 2 · clip c1 · video-07-c1-request.json), and made Frame 1 (WRONG, 0:01.00) of this sample." in pt
          and "it generated second 2 to second 5 of Video A (generation 2 of 2 · clip c2 · video-07-c2-request.json), and made Frame 2 (WRONG, 0:04.00) of this sample." in pt
          and pt.index("PROMPT-C1") < pt.index("PROMPT-C2") and "this sample keeps no correct version's prompt: none of its frames is called RIGHT" in pt, pt[:600])
    meta7 = json.loads((core.DATA / "samples" / str(smo) / "meta.json").read_text(encoding="utf-8"))
    check("each frame of the image is numbered, names its video and the generation that made it (meta), as the prompts name it",
          [(f["n"], f["video"], (f.get("generation") or {}).get("gen"), (f.get("generation") or {}).get("clip")) for f in meta7["frames"]] == [(1, "A", 1, "c1"), (2, "A", 2, "c2")]
          and meta7["frames"][1]["generation"]["t_from"] == 2.0, str(meta7["frames"])[:300])
    im7 = Image.open(core.DATA / "samples" / str(smo) / "composite.png").convert("RGB")
    check("each frame's tag has a second line under FRAME n (its video, source and generation, on a dark ground)", im7.getpixel((27, 55)) == (255, 45, 45)
          and im7.getpixel((27, 85)) == (46, 50, 60), f"{im7.getpixel((27, 55))} {im7.getpixel((27, 85))}")
    dg7 = [r for k, r in _qcs_records().items() if k == f"S{smo}"]
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
    check("a RIGHT frame taken from Video A: the correct version's box gives Video A's generation that made it, named as the correct version's prompt",
          [x["gen"] for x in pp3.get("fixed_segments") or []] == [1]
          and "This is a prompt of the correct version (the generation the frames called RIGHT come from): it generated second 0 to second 2 of Video A (generation 1 of 2"
          in pp3.get("text", "").split("the correct version's prompt box")[-1] and "Frame 3 (RIGHT, 0:00.50)" in pp3.get("text", ""), pp3.get("text", "")[-500:])
    dg_lines = _qcs.records_of(_qcs._digest()).get(f"S{smo2}", "")
    check("the keeper's digest names each prompt in a word with its generation of the take and the seconds it made, nothing explained twice",
          f"S{smo2}-P1 · FLAWED · generation 2 of 2 of Video A (2–5 s) · made Frames 1 and 2 of S{smo2}" in dg_lines and "holds generations of a take made of" not in dg_lines, dg_lines[-700:])
    s, d, _ = req("GET", "/api/generation-assets?run=20261001-000000-07&video=07&wrong=" + quote(clean7))
    check("a take joined from two generations offers each picture its generations were sent once, named with every clip that sent it (the first "
          "clip's request kept with it)", s == 200 and [(a["request"], a["label"]) for a in d.get("assets", [])]
          == [("video-07-c1-request.json", "c1, c2 · Image 1 · pack.png · its original file is not recorded")], str(d)[:300])
    s, d, _ = req("PATCH", f"/api/misses/{sm}", {"wrong_prompt": "One prompt typed by the admin."})
    check("a single prompt typed over the generations' prompts replaces them", s == 200 and d["miss"]["wrong_segments"] == [] and d["miss"]["wrong_prompt"] == "One prompt typed by the admin.", str(d)[:200])
    req("DELETE", f"/api/misses/{sm}")

    print("\n[Example frames only: the prompts are Example prompts, never the wrong or the corrected one]")
    c2take = str(v7 / "c2" / "a1" / "trimmed.mp4")
    s, d, _ = req("POST", "/api/misses", {"run": "20261001-000000-07", "video": "07", "title": "Two videos kept as examples", "model": "wan3_0_prime",
                                          "wrong_path": clean7, "fixed_path": c2take, "wrong_prompt": "EX-PROMPT-A the can on the desk.",
                                          "fixed_prompt": "EX-PROMPT-B the can in the hand."})
    em = d["miss"]["id"] if s == 200 else 0

    def emoment(frames):
        st, dd, _ = req("POST", f"/api/misses/{em}/moments", {"frames": frames, "details": "kept to remember"})
        return dd["moment"] if st == 200 else {}
    ex1 = emoment([{"src": "wrong", "role": "example", "t": 0.5}, {"src": "wrong", "role": "example", "t": 1.0}])
    check("a sample of Example frames only has Example prompts on both sides", ex1.get("prompt_roles") == {"wrong": "example", "fixed": "example"}, str(ex1.get("prompt_roles")))
    check("an Examples sample takes all its frames from one video and speaks through that video's one prompt box", ex1.get("prompt_sides") == ["wrong"], str(ex1.get("prompt_sides")))
    sx, dx, _ = req("POST", f"/api/misses/{em}/moments", {"frames": [{"src": "wrong", "role": "example", "t": 1.0}, {"src": "fixed", "role": "example", "t": 0.5}]})
    sxb, dxb, _ = req("PATCH", f"/api/moments/{ex1['moment_id']}", {"frames": [{"src": "wrong", "role": "example", "t": 1.0}, {"src": "fixed", "role": "example", "t": 0.5}]})
    check("Example frames from two videos are refused, saved or updated, with the rule in the answer",
          sx == 400 and "takes all its frames from one video" in str(dx.get("error", "")) and "one prompt box" in str(dx.get("error", "")) and sxb == 400, f"{sx} {dx} {sxb}")

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
    check("the judge reads the Examples sample's one box as an example prompt, never as the flawed or the correct version's, and never Video B's box",
          s == 200 and (d.get("sample") or {}).get("prompt_roles") == {"wrong": "example", "fixed": "example"} and "this sample's frames are all Examples" in et
          and (d.get("sample") or {}).get("prompt_sides") == ["wrong"] and "takes all its frames from one video, here Video A" in et
          and "the example prompt box of Video A" in et and "example prompt box of Video B" not in et and "EX-PROMPT-A" in et and "EX-PROMPT-B" not in et
          and "no flawed and no correct prompt" in et and "the flawed version's prompt box" not in et
          and not (d.get("sample") or {}).get("fixed_prompt")
          and "a suggestion of what could have changed or how it could have been done another way, neither a bad generation nor a correct one" in et
          and "the prompt of the wrong take" not in et and "the prompt of the corrected take" not in et, et[:500])
    s2, d2, _ = req("POST", f"/api/misses/{em}/moments", {"frames": [{"src": "wrong", "role": "example", "t": 1.0}, {"src": "wrong", "role": "right", "t": 1.5}]})
    s2b, d2b, _ = req("PATCH", f"/api/moments/{ex1['moment_id']}", {"frames": [{"src": "wrong", "role": "example", "t": 1.0}, {"src": "fixed", "role": "wrong", "t": 0.5}]})
    check("a sample that mixes an Example with a Wrong or Right frame is refused, saved or updated, with the rule in the answer",
          s2 == 400 and "either a miss with its correct version" in str(d2.get("error", "")) and "neither a bad generation nor a correct one" in str(d2.get("error", ""))
          and s2b == 400, f"{s2} {d2} {s2b}")
    ex3 = emoment([{"src": "wrong", "role": "wrong", "t": 1.0}, {"src": "fixed", "role": "right", "t": 0.5}])
    check("a sample of a Wrong and a Right frame: the flawed version's box and the correct version's box",
          ex3.get("prompt_roles") == {"wrong": "flawed", "fixed": "correct"}, str(ex3.get("prompt_roles")))
    dlines = _qcs_records()
    check("the keeper's digest names the Examples sample's one box as an example prompt, and a pair as the flawed and the correct version's boxes",
          "PROMPTS SAVED: EXAMPLE (Video A), this Examples sample's one box; sample_origin reads it" in dlines.get(ex1.get("id"), "")
          and "FLAWED" not in dlines.get(ex1.get("id"), "") and "neither a bad generation nor a correct one" in _qcs.LIB_HEADER
          and "EXAMPLE (Video A or B: an Examples sample's one box)" in _qcs.LIB_HEADER
          and "PROMPTS SAVED: FLAWED + CORRECT; sample_origin reads them" in dlines.get(ex3.get("id"), ""), dlines.get(ex1.get("id"), "")[-300:])
    m_ex = json.loads((core.DATA / "samples" / str(ex1["moment_id"]) / "meta.json").read_text(encoding="utf-8"))
    check("an Example sample's frames are tagged EXAMPLE in its record", [f["role"] for f in m_ex["frames"]] == ["example", "example"], str(m_ex["frames"])[:200])
    check("with a sample whose frames are Wrong and Right, the database keeps the flawed version's box as 'flawed' and the correct version's as 'correct'",
          db_roles() == ("flawed", "correct"), str(db_roles()))
    s3, d3, _ = req("GET", f"/api/qc/samples/{ex3.get('id')}?prompts=1", key=kq)
    t3 = (d3.get("sample") or {}).get("prompts_text", "")
    check("the judge reads the flawed version's prompt as the one that generated the flawed version and the correct version's as the correct one's",
          "the flawed version's prompt box (the generations the frames called WRONG come from: they generated the flawed version) (whole, as the admin saved it):\nEX-PROMPT-A" in t3
          and "the correct version's prompt box (the generations the frames called RIGHT come from: they generated the correct version) (whole, as the admin saved it):\nEX-PROMPT-B" in t3, t3[:600])
    from loupe_app import qc as _qce
    lib_ex = {x["id"]: x for x in _qce.library()}
    bl = _qce.sample_lines(lib_ex[ex1["id"]]) if ex1.get("id") in lib_ex else ""
    check("the brief says the one prompt kept is an example prompt, Video A's", "the prompt box of Video A, an example prompt" in bl and "the prompt box of Video B" not in bl, bl[:600])
    # one record per sample wherever an agent is handed samples (the user, 2026-10-29): which frame of which sample, its video, the prompt that made it
    recs = _qcs_records()
    r3 = recs.get(ex3.get("id"), "")
    full3 = _qce.sample_record(lib_ex[ex3["id"]], "full") if ex3.get("id") in lib_ex else ""
    check("each sample reaches the keeper as one record that opens and closes with its id and names its generation miss and that report's other sample",
          r3.startswith(f"=== SAMPLE {ex3['id']} · generation miss R{em} (sample 2 of 2 of that report; its other sample: {ex1['id']})")
          and r3.endswith(f"=== END OF SAMPLE {ex3['id']} ===") and f"THE GENERATION MISS R{em} OF {ex3['id']}: " in r3
          and f"WHAT {ex3['id']} IS: " in r3 and f"WHERE {ex3['id']} CAME FROM: " in r3, r3[:400])
    check("the frames in a record name their video and the label of the prompt that made them",
          f"  {ex3['id']} · Frame 1 WRONG 0:01.00, Video A" in r3 and f"made by {ex3['id']}-P1 (FLAWED)" in r3 and f"  {ex3['id']} · Frame 2 RIGHT 0:00.50, Video B" in r3
          and f"made by {ex3['id']}-P2 (CORRECT)" in r3 and f"  {ex3['id']}-P1 · FLAWED · one text · made Frame 1 of {ex3['id']}" in r3, r3[:1400])
    p1 = full3.split(f"--- {ex3['id']}-P1 (sample {ex3['id']}, generation miss R{em}) · ")[-1].split(f"--- end of {ex3['id']}-P1 (sample {ex3['id']}) ---")[0]
    p2 = full3.split(f"--- {ex3['id']}-P2 (sample {ex3['id']}, generation miss R{em}) · ")[-1].split(f"--- end of {ex3['id']}-P2 (sample {ex3['id']}) ---")[0]
    check("its whole record gives each prompt under that label, in its box, with the frames it made, and nothing of the other",
          p1.startswith("the flawed version's prompt box: the prompts of the generations the frames called WRONG come from, which generated the flawed version ---")
          and "This is a prompt of the flawed version" in p1 and "Frame 1 (WRONG, 0:01.00)" in p1 and "EX-PROMPT-A" in p1 and "EX-PROMPT-B" not in p1
          and p2.startswith("the correct version's prompt box: the prompts of the generations the frames called RIGHT come from, which generated the correct version ---")
          and "This is a prompt of the correct version" in p2 and "Frame 2 (RIGHT, 0:00.50)" in p2 and "EX-PROMPT-B" in p2 and "EX-PROMPT-A" not in p2, full3[:1800])
    check("the admin's words are tied to the frames they speak of",
          "wrong here (what is wrong at the marked place of Frame 1 (WRONG, 0:01.00), called WRONG): kept to remember" in full3, full3[:900])
    both = _qce.records_of("\n\n".join(_qce._prompts_text(k, lib_ex[k]) for k in (ex1["id"], ex3["id"]) if k in lib_ex))
    check("records handed to the judge together stay apart: each holds its own frames and prompts and none of the other's",
          set(both) == {ex1["id"], ex3["id"]} and f"made by {ex1['id']}-P1" in both.get(ex1["id"], "") and f"{ex3['id']}-P" not in both.get(ex1["id"], "")
          and f"{ex1['id']}-P" not in both.get(ex3["id"], "") and "EX-PROMPT-B" not in both.get(ex1["id"], ""), str(sorted(both)))
    bt = _qce.brief({"model": "wan3_0_prime"}, 50)
    check("the QC brief hands its samples as the same records", ex3["id"] in _qce.records_of(bt.get("text", "")) and "one record per sample" in bt.get("text", ""),
          bt.get("text", "")[:300])
    # the user, 2026-10-29/30: nothing out of sync. Every text an agent reads names a prompt by what its box holds (the flawed version's, the correct
    # version's, an example), never by the words of before (the two takes, the correct take, Video A's prompt ...)
    OLD_WORDS = ("two takes", "both takes", "correct take", "flawed take", "example take", "corrected take", "wrong take", "the corrected prompt",
                 "Video A's prompt", "Video B's prompt", "Prompt of Video A", "Prompt of Video B", "TWO TAKES", "BOTH TAKES", "CORRECT TAKE",
                 "FLAWED TAKE", "EXAMPLE TAKE", "PROMPTS OF BOTH")
    agent_texts = {"the judge's role": _qce.ROLE, "the library header": _qce.LIB_HEADER, "the collector's role": _qce.EXTRACT_ROLE,
                   "the brief's prompt words": " ".join(_qce.PROMPTS_SAVED.values()) + " " + _qce.EXAMPLE_PROMPT, "the digest": "\n\n".join(recs.values()),
                   "a full record": full3, "the judge's text of an Examples sample": et, "the judge's text of a pair": t3,
                   "the records handed together": "\n\n".join(both.values()), "the QC brief": bt.get("text", ""), "the brief's lines": bl,
                   "the refusal of a mixed sample": str(d2.get("error", ""))}
    stale = [f"{name}: {w}" for name, txt in agent_texts.items() for w in OLD_WORDS if w in (txt or "")]
    check("no text an agent reads names a prompt by the old words (the two takes, the correct take, Video A's prompt ...)", not stale, "; ".join(stale)[:600])
    src_stale = [f"{f.name}: {w}" for f in sorted((Path(__file__).resolve().parent.parent / "loupe_app").glob("*.py")) for w in OLD_WORDS if w in f.read_text(encoding="utf-8")]
    check("and no code, docstring or comment of Loupe does either", not src_stale, "; ".join(src_stale)[:600])
    req("DELETE", f"/api/moments/{ex3['moment_id']}")
    check("deleting the other samples makes the report's prompts examples again in the database", db_roles() == ("example", "example"), str(db_roles()))
    # the one box of an Examples sample gives the prompt of each generation its Example frames come from, and no other (the user, 2026-10-29: "if two or
    # more samples are from one video generation only make sure to only include that generation prompts in the prompt filler not more")
    req("PATCH", f"/api/misses/{em}", {"wrong_segments": [{"clip": "c1", "attempt": 1, "request": "video-07-c1-request.json", "t_from": 0, "t_to": 1.5, "prompt": "EXSEG-ONE the can on the desk."},
                                                        {"clip": "c2", "attempt": 1, "request": "video-07-c2-request.json", "t_from": 1.5, "t_to": 3.0, "prompt": "EXSEG-TWO the can in the hand."}]})
    s, d, _ = req("GET", f"/api/qc/samples/{ex1.get('id')}?prompts=1", key=kq)
    et2 = (d.get("sample") or {}).get("prompts_text", "")
    check("an Examples sample's one box gives the prompt of the generation its Example frames come from, named as an example, and not the one no frame comes from",
          "EXSEG-ONE" in et2 and "EXSEG-TWO" not in et2 and len((d.get("sample") or {}).get("wrong_segments") or []) == 1
          and "This is an example prompt (the generation the frames called EXAMPLE come from: a suggestion" in et2
          and "it generated second 0 to second 1.5 of Video A (generation 1 of 2 · clip c1 · video-07-c1-request.json), and made Frame 1 (EXAMPLE, 0:00.50) and Frame 2 (EXAMPLE, 0:01.00) of this sample." in et2,
          et2[:900])
    from loupe_app import runs as _runs
    # no video generation prompt is ever cut (the user, 2026-10-29): a long generation's prompt, a long box, the judge's template, a run's request prompt
    long_seg = "SEG-LONG " + "the can stands on the desk, as tall as the mug. " * 700 + "SEG-LONG-END"
    req("PATCH", f"/api/misses/{em}", {"wrong_segments": [{"clip": "c1", "attempt": 1, "request": "video-07-c1-request.json", "t_from": 0, "t_to": 1.5, "prompt": long_seg},
                                                        {"clip": "c2", "attempt": 1, "request": "video-07-c2-request.json", "t_from": 1.5, "t_to": 3.0, "prompt": "EXSEG-TWO the can in the hand."}]})
    s, d, _ = req("GET", f"/api/qc/samples/{ex1.get('id')}?prompts=1", key=kq)
    rec_long = (d.get("sample") or {}).get("record", "")
    check("a generation's prompt of over 30000 characters is stored and handed to the judge whole, in the sample's record",
          len(long_seg) > 30000 and long_seg in rec_long and long_seg in str((d.get("sample") or {}).get("prompts_text", "")), str(len(rec_long)))
    long_box = "\n\n".join(f"This is the prompt that generated second {k} to second {k + 1} of Video A.\n" + "P" * 60000 for k in range(4))
    s, d, _ = req("PATCH", f"/api/misses/{em}", {"wrong_prompt": long_box})
    check("a box that holds the prompts of several generations is kept whole, however long (here 240000 characters)",
          s == 200 and d["miss"]["wrong_prompt"] == long_box.strip(), str(len((d.get("miss") or {}).get("wrong_prompt", ""))))
    check("the judge's template is kept whole, however long", _qce.template_from({"template": "T" * 50000}, {})["template"] == "T" * 50000)
    rq7 = sorted((_runs.run_dir("20261001-000000-07") / "50-requests").glob("video-07*request.json"))
    if rq7:
        j7 = json.loads(rq7[0].read_text(encoding="utf-8"))
        (j7.setdefault("params", {}))["prompt"] = "REQ-LONG " + "x " * 20000 + "REQ-LONG-END"
        (rq7[0].parent / "video-98-request.json").write_text(json.dumps(j7), encoding="utf-8")
    pr98 = _qce._request_prompt(_runs.run_dir("20261001-000000-07"), "98")
    check("a run's request prompt reaches the judge whole (sample_origin, a request_prompt need)", pr98.endswith("REQ-LONG-END") and len(pr98) > 40000, str(len(pr98)))
    req("DELETE", f"/api/misses/{em}")

    print("\n[which seconds each generation made, read from how the take was joined; each box holds the generations its frames come from, and no other]")
    from loupe_app import misses as _ms
    v8 = atp_run / "60-outputs" / "video-08"
    for att in ("a1", "a2"):            # c1 cut in two around c2 (c1a, c2, c1b), as a with-inserts video is joined; a1 is an older attempt of c1
        mkclip(v8 / "c1" / att / "raw.mp4", 4)
        mkclip(v8 / "c1" / att / "c1a.mp4", 1)
        mkclip(v8 / "c1" / att / "c1b.mp4", 2)
    mkclip(v8 / "c2" / "a1" / "raw.mp4", 2)
    mkclip(v8 / "c2" / "a1" / "c2.mp4", 1.5)
    (v8 / "concat-list.txt").write_text("file 'c1/a1/c1a.mp4'\nfile 'c2/a1/c2.mp4'\nfile 'c1/a1/c1b.mp4'\n", encoding="utf-8")      # the older a1 join's list, left beside the copy
    (v8 / "a2").mkdir(parents=True, exist_ok=True)
    (v8 / "a2" / "concat-list.txt").write_text("file '../c1/a2/c1a.mp4'\nfile '../c2/a1/c2.mp4'\nfile '../c1/a2/c1b.mp4'\n", encoding="utf-8")
    def mkcol(path, secs, color):         # a clip of its own colour: never byte for byte another file
        path.parent.mkdir(parents=True, exist_ok=True)
        cp = subprocess.run([media.find_tool("ffmpeg"), "-y", "-loglevel", "error", "-f", "lavfi", "-i", f"color=c={color}:s=360x640:r=30:d={secs}",
                             "-pix_fmt", "yuv420p", str(path)], capture_output=True)
        assert cp.returncode == 0, cp.stderr[-300:]
    mkcol(v8 / "joined-a1.mp4", 4.5, "0x604020")             # the a1 join, older than the clean video
    os.utime(v8 / "joined-a1.mp4", (1_700_000_000, 1_700_000_000))
    mkclip(v8 / "a2" / "clean.mp4", 4.5)
    shutil.copyfile(v8 / "a2" / "clean.mp4", v8 / "clean.mp4")         # the clean video copied up from the attempt folder it was joined in
    mkcol(v8 / "preview.mp4", 4.5, "0x206040")                # a render of the clean video (as long, made after it)
    for name, text in (("video-08-c1-request.json", "J-C1-A1 the first attempt of c1."), ("video-08-c1-a2-request.json", "J-C1-A2 the can stands on the desk."),
                       ("video-08-c2-request.json", "J-C2 the hand lifts the can.")):
        (atp_run / "50-requests" / name).write_text(json.dumps({"video": "08", "clip": name.split("-")[2], "params": {"model": "wan3_0_prime", "prompt": text,
                                                                                                                     "medias": [{"role": "image_references", "value": "m1"}]}}), encoding="utf-8")
    (v8 / "result.json").write_text(json.dumps({"video": "08", "jobs": [{"clip": "c1", "attempts": 2}, {"clip": "c2", "attempts": 1}]}), encoding="utf-8")
    run7 = "20261001-000000-07"
    sg = runs.prompt_segments(run7, "08", str(v8 / "clean.mp4"))
    by = {s["clip"]: s for s in sg.get("segments") or []}
    check("a clip cut around another one is one generation with two spans, read from the join list of the attempt folder the clean video was copied from",
          sg.get("spans_known") and "a2/concat-list.txt" in sg.get("source", "") and by.get("c1", {}).get("spans") == [[0.0, 1.0], [2.5, 4.5]]
          and by["c1"]["attempt"] == 2 and by["c1"]["request"] == "video-08-c1-a2-request.json" and by.get("c2", {}).get("spans") == [[1.0, 2.5]]
          and [(s["clip"], s["gen"], s["of"]) for s in sg["segments"]] == [("c1", 1, 2), ("c2", 2, 2)], str(sg)[:500])
    sp = runs.prompt_segments(run7, "08", str(v8 / "preview.mp4"))
    check("a render of the clean video (as long, made after it) is read through the clean video's join", sp.get("spans_known") and "through the clean video" in sp.get("source", "")
          and [s["attempt"] for s in sp["segments"]] == [2, 1], str(sp)[:300])
    so = runs.prompt_segments(run7, "08", str(v8 / "joined-a1.mp4"))
    check("an older join beside its own list is read from that list, with the attempt its pieces sit in", so.get("spans_known") and so["source"] == "the concat list concat-list.txt"
          and so["segments"][0]["attempt"] == 1 and so["segments"][0]["request"] == "video-08-c1-request.json", str(so)[:300])
    v9 = atp_run / "60-outputs" / "video-09"         # a join record as awl.py concat writes it: the output's sha1 and every piece with its clip and attempt
    mkclip(v9 / "c1" / "a1" / "raw.mp4", 2)
    mkclip(v9 / "c2" / "a3" / "raw.mp4", 3)
    mkclip(v9 / "joined.mp4", 5)
    (v9 / "joined.mp4.join.json").write_text(json.dumps({"file": "joined.mp4", "sha1": media.sha1_file(v9 / "joined.mp4"), "pieces": [
        {"file": str(v9 / "c1" / "a1" / "raw.mp4"), "clip": "c1", "attempt": 1, "t_from": 0.0, "t_to": 2.0},
        {"file": str(v9 / "c2" / "a3" / "raw.mp4"), "clip": "c2", "attempt": 3, "t_from": 2.0, "t_to": 5.0}]}), encoding="utf-8")
    sr = runs.prompt_segments(run7, "09", str(v9 / "joined.mp4"))
    check("a join record found by the file's own bytes gives each piece's clip, attempt and seconds", sr.get("spans_known") and "join record" in sr.get("source", "")
          and [(s["clip"], s["attempt"], s["spans"]) for s in sr["segments"]] == [("c1", 1, [[0.0, 2.0]]), ("c2", 3, [[2.0, 5.0]])], str(sr)[:300])
    v10 = atp_run / "60-outputs" / "video-10"         # nothing records the join and the clips' lengths do not add up to the file: never guessed
    mkclip(v10 / "c1" / "a1" / "raw.mp4", 3)
    mkclip(v10 / "c2" / "a1" / "raw.mp4", 3)
    mkclip(v10 / "clean.mp4", 5)
    su = runs.prompt_segments(run7, "10", str(v10 / "clean.mp4"))
    bu = _ms.prompt_boxes(None, run7, "10", {"wrong": str(v10 / "clean.mp4")}, [{"src": "wrong", "role": "wrong", "t": 1.0}])
    check("a join that cannot be read gives no seconds, and the box is left unwritten with the reason", su.get("kind") == "clips" and su.get("spans_known") is False
          and not su.get("segments") and bu["wrong"]["text"] == "" and "could not be read" in " ".join(bu["wrong"]["problems"]), str(su)[:200] + " / " + str(bu["wrong"])[:200])
    clean8 = str(v8 / "clean.mp4")
    # the user's case: a Wrong frame in generation 1, a Right frame in generation 2 and another Wrong frame back in generation 1, one video
    bx = _ms.prompt_boxes(None, run7, "08", {"wrong": clean8}, [{"src": "wrong", "role": "wrong", "t": 0.5}, {"src": "wrong", "role": "right", "t": 1.5},
                                                                {"src": "wrong", "role": "wrong", "t": 3.0}])
    check("the flawed version's box holds the generation the Wrong frames come from, and the correct version's box the one the Right frame comes from, both of one video",
          [(s["clip"], s["attempt"]) for s in bx["wrong"]["segments"]] == [("c1", 2)] and [(s["clip"], s["attempt"]) for s in bx["fixed"]["segments"]] == [("c2", 1)]
          and bx["wrong"]["role"] == "flawed" and bx["fixed"]["role"] == "correct" and "J-C2" not in bx["wrong"]["text"] and "J-C1" not in bx["fixed"]["text"]
          and bx["wrong"]["text"].startswith("This is a prompt of the flawed version (the generation the frames called WRONG come from): it generated second 0 to second 1 and "
                                             "second 2.5 to second 4.5 of Video A (generation 1 of 2 · clip c1 · video-08-c1-a2-request.json), and made Frame 1 (WRONG, 0:00.50) "
                                             "and Frame 3 (WRONG, 0:03.00) of this sample.\nJ-C1-A2"), str(bx)[:900])
    br = _ms.prompt_boxes(None, run7, "08", {"wrong": clean8}, [{"src": "wrong", "role": "right", "t": 0.5}, {"src": "wrong", "role": "right", "t": 3.0}])
    check("frames called Right only fill the correct version's box alone, with the one generation they come from", not br["wrong"]["used"] and br["wrong"]["text"] == ""
          and br["fixed"]["used"] and [s["clip"] for s in br["fixed"]["segments"]] == ["c1"] and "J-C2" not in br["fixed"]["text"], str(br)[:400])
    s, d, _ = req("POST", "/api/misses/prompt-boxes", {"run": run7, "video": "08", "wrong_path": clean8, "frames": [{"src": "wrong", "role": "right", "t": 0.5}]})
    check("the form's route answers the same boxes", s == 200 and not d["wrong"]["used"] and [x["clip"] for x in d["fixed"]["segments"]] == ["c1"], str(d)[:300])
    s, d, _ = req("POST", "/api/misses", {"run": run7, "video": "08", "title": "The can is right, then grows", "model": "wan3_0_prime", "wrong_path": clean8, "wrong_label": "clean.mp4",
                                          "fixed_prompt": br["fixed"]["text"], "fixed_segments": br["fixed"]["segments"]})
    jm = d["miss"]["id"] if s == 200 else 0
    check("a box holding one generation of a take made of two keeps it beside its text (its seconds and its number say which part of the video it made)",
          s == 200 and len(d["miss"]["fixed_segments"]) == 1 and d["miss"]["fixed_segments"][0]["of"] == 2 and d["miss"]["fixed_segments"][0]["spans"] == [[0.0, 1.0], [2.5, 4.5]]
          and d["miss"]["wrong_prompt"] == "", str(d)[:400])
    s, d, _ = req("POST", f"/api/misses/{jm}/moments", {"frames": [{"src": "wrong", "role": "right", "t": 0.5}, {"src": "wrong", "role": "right", "t": 3.0}], "details": "Right here."})
    jr = d["moment"] if s == 200 else {}
    s, d, _ = req("GET", f"/api/qc/samples/{jr.get('id')}?prompts=1", key=kq)
    js_ = d.get("sample") or {}
    rec = js_.get("record", "")
    check("a sample of frames called Right only: the judge is told it is the correct version only, with the correct version's prompt alone and no flawed one",
          js_.get("prompt_sides") == ["fixed"] and f"WHAT {jr.get('id')} IS: THE CORRECT VERSION ONLY (no frame is called wrong)" in rec
          and f"no flawed version's prompt: none of the frames of {jr.get('id')} is called WRONG" in rec and "the correct version's prompt box: the prompts of the generations the frames called RIGHT come from" in rec
          and "J-C1-A2" in rec and "J-C2" not in rec and f"made by {jr.get('id')}-P1 (the correct version's prompt box)" in rec
          and "this sample keeps no flawed version's prompt" in js_.get("prompts_text", ""), rec[:1600])
    from loupe_app import qc as _qck
    lib_k = {x["id"]: x for x in _qck.library()}
    ek = _qck._extract_prompt(lib_k[jr["id"]]) if jr.get("id") in lib_k else ""
    check("the collector is told such a sample is the correct version only, as its record says (its sample_kind stays scenario for the older pipelines)",
          lib_k.get(jr.get("id"), {}).get("sample_kind") == "scenario" and ek.startswith(f"SAMPLE {jr.get('id')} (the correct version only: its frames are all called RIGHT)"),
          ek[:200] or "not in the library")
    meta8 = json.loads((core.DATA / "samples" / str(jr.get("moment_id")) / "meta.json").read_text(encoding="utf-8")) if jr else {}
    check("each frame's tag names the generation that made it with every span of it", [(f.get("generation") or {}).get("spans") for f in meta8.get("frames", [])] == [[[0.0, 1.0], [2.5, 4.5]]] * 2,
          str(meta8.get("frames"))[:300])
    # a second sample of the report: its Right frame comes from c2, so the correct version's box holds both generations, each naming its sample
    s, d, _ = req("POST", f"/api/misses/{jm}/moments", {"frames": [{"src": "wrong", "role": "right", "t": 2.0}], "details": "Right in c2 too."})
    jr2 = d["moment"] if s == 200 else {}
    s, d, _ = req("GET", f"/api/misses/{jm}")
    m2 = d.get("miss") or {}
    check("two or more samples count together: the box written from a joined take follows them, each frame named with its sample",
          [x["clip"] for x in m2.get("fixed_segments") or []] == ["c1", "c2"] and f"of {jr.get('id')}." in m2.get("fixed_prompt", "")
          and f"Frame 1 (RIGHT, 0:02.00) of {jr2.get('id')}" in m2.get("fixed_prompt", ""), m2.get("fixed_prompt", "")[:600])
    pq = _ms.prompts_of(jr2.get("moment_id")) or {}
    check("each sample reads only the generation its own frames come from", [x["clip"] for x in pq.get("fixed_segments") or []] == ["c2"] and "J-C1" not in pq.get("text", ""), pq.get("text", "")[:400])
    req("DELETE", f"/api/moments/{jr2.get('moment_id')}")
    s, d, _ = req("GET", f"/api/misses/{jm}")
    check("a deleted sample's generation leaves the box Loupe wrote", [x["clip"] for x in (d.get("miss") or {}).get("fixed_segments") or []] == ["c1"]
          and "J-C2" not in (d.get("miss") or {}).get("fixed_prompt", ""), str((d.get("miss") or {}).get("fixed_segments"))[:300])
    req("DELETE", f"/api/misses/{jm}")
    late = media.frame_file(Path(six_take), 999)
    check("a frame asked for past the end of a video is its last picture, never an error", late.is_file())
    s, d, _ = req("POST", "/api/misses", {"run": "20261001-000000-07", "video": "03", "title": "The pen became a can", "model": "wan3_0_prime",
                                          "wrong_path": wrong, "fixed_path": orig, "fixed_label": ov[0]["label"] if ov else ""})
    om = d["miss"]["id"]
    s, d, _ = req("POST", f"/api/misses/{om}/moments", {"frames": [{"src": "wrong", "role": "wrong", "t": 1.0}, {"src": "fixed", "role": "right", "t": 1.0}],
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
            if "rests on samples whose prompts" in prompt:
                return {"result": json.dumps({"relevant": [{"id": sid0, "why": "judged after reading both prompts", "check": "c", "wrong_pattern": "p"}], "advice": "a",
                                              "cause": "shot 1 gave no size", "template_fix": "the can as tall as the mug",
                                              "flags": [{"id": sid0, "what": "FLAG-MARK the right frame's prompt was saved as one text", "effect": "the cause rests on one prompt"},
                                                        {"id": "S999", "what": "a sample that does not exist"},
                                                        {"what": "LIB-FLAG no sample shows the can carried through a doorway"}]}), "total_cost_usd": 0}
            if "QC question" in prompt:
                return {"result": json.dumps({"relevant": [{"id": sid0, "why": "first answer", "check": "c", "wrong_pattern": "p"}], "advice": "a",
                                              "cause": "first cause", "template_fix": "first fix"}), "total_cost_usd": 0}
            return {"result": "ready", "total_cost_usd": 0}
        _qc._run_claude = judge
        s_x, d_x, _ = req("POST", f"/api/misses/{mid0}/moments", {"frames": [{"src": "wrong", "role": "wrong", "t": 1.0}],
                                                             "details": "A second moment of the same miss, which the template answer names in none of its lists."})
        mo_x = d_x["moment"]["moment_id"] if s_x == 200 else 0
        s, d, _ = req("POST", "/api/qc/consult", {"purpose": "template", "run": "r", "video": "05", "model": "wan3_0_prime",
                                                  "question": "BRIEF-MARK video 05: he sets the can on the desk; the goal is the can's true size.",
                                                  "structure": "STRUCTURE-MARK Shot 1: ... Shot 2: ...", "video_model": {"id": "wan3_0_prime", "name": "MODEL-MARK"}}, key=kq)
        step2 = next((p for p in seen if "Step 2 of the template request" in p), "")
        check("a template request: the judge is briefed with the agent's brief, the video model and its structure, then given both prompts it chose, whole",
              s == 200 and any("TEMPLATE REQUEST" in p and "BRIEF-MARK" in p and "STRUCTURE-MARK" in p and "MODEL-MARK" in p for p in seen) and longw.strip() in step2
              and "PROMPT-F" in step2 and "Not sent: S999" in step2, str(d)[:300])
        check("the answer is the judge's template with its basis (only samples that exist), notes and changes, and the samples it read; none is sent twice",
              d.get("purpose") == "template" and d.get("template", "").startswith("TEMPLATE-MARK") and [x["id"] for x in d.get("basis", [])] == [sid0]
              and d.get("read") == [sid0] and d.get("changes") and d.get("notes") == "keep the mug line" and not any("rests on samples" in p for p in seen)
              and d.get("prompts_judged") == [sid0] and bool(d["basis"][0].get("prompt_sides")), str(d)[:300])
        s, d, _ = req("GET", "/api/qc/keeper")
        check("the consult log keeps the template request under its own purpose", s == 200 and any(c["purpose"] == "template" for c in d["consults"]))
        tcj = next((c for c in d["consults"] if c["purpose"] == "template"), {}) if s == 200 else {}
        want_unj = [x["id"] for x in _qc.library() if x["id"] != sid0]
        check("the samples a template answer left unjudged are kept with the consult as a gap the Keeper page shows",
              want_unj == [f"S{mo_x}"] and ((tcj.get("asks") or {}).get("unjudged") or []) == want_unj, f"{(tcj.get('asks') or {}).get('unjudged')} vs {want_unj}")
        if mo_x:
            req("DELETE", f"/api/moments/{mo_x}")
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
        check("the judge's flags on a sample's record reach the answer (only samples that exist, or the library as a whole) and the consult log",
              d.get("flags") == [{"id": sid0, "what": "FLAG-MARK the right frame's prompt was saved as one text", "effect": "the cause rests on one prompt"},
                                 {"id": "", "what": "LIB-FLAG no sample shows the can carried through a doorway", "effect": ""}]
              and any((c.get("asks") or {}).get("flags") for c in (req("GET", "/api/qc/keeper")[1] or {}).get("consults", [])), str(d.get("flags")))
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
        check("a sample whose prompts the judge already fetched in that consult is not sent again, and prompts_judged still names it (it read them)",
              s == 200 and not any("rests on samples" in p for p in seen) and d.get("prompts_judged") == [sid0] and not d.get("prompts_unread")
              and d.get("relevant"), str(d)[:200])

        def judge_bad(prompt, *a, **k):
            if "rests on samples" in prompt:
                return {"result": "not json", "total_cost_usd": 0}
            return judge(prompt)
        _qc._run_claude = judge_bad
        s, d, _ = req("POST", "/api/qc/consult", {"run": "r", "video": "05", "question": "again"}, key=kq)
        check("a second answer that does not parse keeps the first one", s == 200 and d["relevant"][0]["why"] == "first answer" and d.get("prompts_judged") == [sid0], str(d)[:200])
        pj, pu = _qc._prompts_seen({"relevant": [{"id": sid0}, {"id": "S99999"}]}, "relevant", [], [], [])
        check("a cited sample whose prompts the judge was not given is named unread (prompts_unread), one it was given judged; the final round reads up to six",
              pj == [] and pu == [sid0] and _qc._prompts_seen({"relevant": [{"id": sid0}]}, "relevant", [], [], [sid0]) == ([sid0], [])
              and _qc.PROMPTS_READ == _qc.TEMPLATE_READ == 10 and "up to 10, in the order you cite them" in _qc.ROLE
              and "naming the samples (at most 10, best first)" in _qc.ROLE, f"{pj} {pu}")
        qc_src = (Path(__file__).resolve().parent.parent / "loupe_app" / "qc.py").read_text(encoding="utf-8")
        check("the admin's limits (2026-10-30): a keeper call may run 240 s, a session is replaced after 250 000 characters, every keeper call takes "
              "that timeout", _qc.KEEPER_CALL_TIMEOUT == 240 and _qc.ROT_CHARS == 250_000 and "timeout: int = KEEPER_CALL_TIMEOUT" in qc_src
              and "timeout=150" not in qc_src, str((_qc.KEEPER_CALL_TIMEOUT, _qc.ROT_CHARS)))
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
        _qc.keeper_reset("test")        # the sections above booted sessions only their fake keeper knew: the real one starts its own
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
        # the admin, 2026-11-02: a keeper whose saved conversation is gone starts a fresh session by itself; here the real claude CLI is given a
        # session id it never saved, as after its transcript was cleaned up
        import uuid as _uuid
        st_k = _qc.core.setting_get("keeper")
        gone_sid = str(_uuid.uuid4())
        st_k["session_id"] = gone_sid
        _qc.core.setting_set("keeper", st_k)
        s, d, _ = req("POST", "/api/qc/consult", {"run": "r", "video": "03", "model": "wan3_0_prime", "question": "The product changes colour after a cut. Which samples apply?"}, key=kq)
        now_sid = _qc.core.setting_get("keeper").get("session_id")
        check("a real keeper whose saved conversation is gone is replaced by a fresh session that answers the same question, and says so",
              s == 200 and d.get("mode") == "keeper" and "was not found by the claude CLI" in d.get("keeper_restarted", "") and now_sid and now_sid != gone_sid
              and any(r["id"] == "S1" for r in d.get("relevant", [])), str(d)[:400])
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
    print("\n[the product sample band: how many images, each one's whole name]")
    from loupe_app import compose as _cmp
    from PIL import Image as _Im, ImageDraw as _Dr
    _dr = _Dr.Draw(_Im.new("RGB", (10, 10)))
    _long = "Image 4 · = image4-a-much-longer-product-file-name-with-many-parts-front-view-padded-v2"
    _ls = _cmp._wrap_hard(_dr, _long, _cmp.font(15, True), 160)
    check("a product image's name is wrapped to its picture's width and never cut (the admin, 2026-10-30)",
          len(_ls) > 1 and "".join(_ls).replace(" ", "") == _long.replace(" ", "") and all(_dr.textlength(x, font=_cmp.font(15, True)) <= 160 for x in _ls), str(_ls))
    _fr = [{"img": _Im.new("RGB", (720, 1280), (120, 110, 100)), "role": "right", "desc": "as generated", "t": 0.0, "shapes": [], "n": 1, "video": "A", "gen": None}]
    _one, _, _ = _cmp.build(_fr, {"title": "t", "sample": "S1"}, "words", "", product_samples=[(_Im.new("RGB", (400, 500)), "Image 2 · = a-name")])
    _three, _, _ = _cmp.build(_fr, {"title": "t", "sample": "S1"}, "words", "", product_samples=[(_Im.new("RGB", (400, 500)), "Image 2 · = a-name"),
                                                                                                (_Im.new("RGB", (400, 500)), "Image 3 · = b-name"), (_Im.new("RGB", (300, 300)), _long)])
    check("the band of three product images with a long name grows to hold every name whole", _three.height >= _one.height, f"{_one.size} {_three.size}")
    print("\n[the product images by the client's original files; the judge decides which and how many (the admin, 2026-10-30)]")
    from loupe_app import compose as _cmp_band, misses as _pm, qc as _pq
    from PIL import Image as _PI
    ref9 = TMP / "reference-folder"
    v9 = atp_run / "55-references" / "video-09" / "a1"
    v9.mkdir(parents=True, exist_ok=True)
    _PI.new("RGB", (300, 400), (200, 150, 120)).save(ref9 / "person.png")
    _PI.new("RGB", (300, 400), (210, 160, 130)).save(v9 / "image1-character-person.png")
    _PI.new("RGB", (300, 400), (0, 0, 250)).save(v9 / "image2-product-P1-front-padded.png")
    take9 = atp_run / "60-outputs" / "video-09" / "a1" / "raw.mp4"
    mkvideo(take9, "0x302050", "white")
    (atp_run / "50-requests" / "video-09-request.json").write_text(json.dumps({"params": {"model": "wan3_0_prime", "duration": 5, "prompt": "Video nine: the man of Image 1 holds the can of Image 2.",
        "medias": [{"role": "video_references", "value": "v9"},
                   {"role": "image_references", "value": "m91", "source": {"original": str(ref9 / "person.png"), "asset_id": "A28", "prepared": "cropped above the collar", "kind": "character"}},
                   {"role": "image_references", "value": "m92", "build": f"from {ref9 / 'product-front.png'} (A14): padded to 9:16 on white"}]}}))
    (atp_run / "50-requests" / "video-09-uploads.json").write_text(json.dumps({"video": "09", "schema": 1, "uploads": {
        "a1 Image 1: person": {"media_id": "m91", "file": str(v9 / "image1-character-person.png"), "role": "image_references"},
        "a1 Image 2: P1 front padded": {"media_id": "m92", "file": str(v9 / "image2-product-P1-front-padded.png"), "role": "image_references"}}}))
    ledger_add("video-09-request.json", "job-909")
    srcs9 = runs.input_sources(atp_run, "09")
    r92, r91 = srcs9.get("m92") or {}, srcs9.get("m91") or {}
    check("each input's source is read from the request (a source record, else a build note) and found by its media id and by the file the uploads record names",
          (r92.get("image"), r92.get("asset_id"), r92.get("name"), r92.get("prepared"), r92.get("recorded")) == ("Image 2", "A14", "product-front.png", "padded to 9:16 on white", "build")
          and srcs9.get(runs._file_key(v9 / "image2-product-P1-front-padded.png")) == r92
          and (r91.get("recorded"), r91.get("name"), r91.get("kind"), r91.get("prepared")) == ("source", "person.png", "character", "cropped above the collar"), str(srcs9)[:700])
    fs_prep = runs.file_source(str(v9 / "image2-product-P1-front-padded.png"), "20261001-000000-07")
    fs_orig = runs.file_source(str(ref9 / "product-front.png"), "20261001-000000-07")
    fs_none = runs.file_source(str(TMP / "elsewhere.png"), "20261001-000000-07")
    check("a picked file names where it came from: a prepared input its record, one of the client's own files itself with its asset id, any other file nothing",
          (fs_prep.get("image"), fs_prep.get("name")) == ("Image 2", "product-front.png") and (fs_orig.get("recorded"), fs_orig.get("asset_id")) == ("the client's file", "A14")
          and fs_none == {}, f"{fs_prep} | {fs_orig} | {fs_none}")
    lab_auto, _x = _pm.product_label({"path": ref9 / "product-front.png", "label": "Test can", "asset_id": "A14", "run": "20261001-000000-07", "video": "09"}, "")
    lab_prep, _x = _pm.product_label({"path": v9 / "image2-product-P1-front-padded.png", "label": "Image 2 · P1 front padded"}, "20261001-000000-07")
    lab_none, _x = _pm.product_label({"path": TMP / "elsewhere.png", "label": "Image 3 · = elsewhere"}, "20261001-000000-07")
    check("a product image is named by its request input, its asset id and the client's original file name, never a folder; the automatic pick (the "
          "original itself) gets the input the run made of it; a file with no record says so",
          lab_auto == "Image 2 · A14 · product-front.png" and lab_prep == "Image 2 · A14 · product-front.png"
          and lab_none == "Image 3 · = elsewhere · its original file is not recorded", f"{lab_auto} | {lab_prep} | {lab_none}")
    s, d, _ = req("GET", "/api/generation-assets?run=20261001-000000-07&video=09&wrong=" + quote(str(take9)))
    ga9 = {a["name"]: a for a in d.get("assets") or []}
    gp9 = ga9.get("image2-product-P1-front-padded.png") or {}
    gc9 = ga9.get("image1-character-person.png") or {}
    check("the images offered are the two sent with the generation, each named by its request input, its asset id and the client's original file name, "
          "with what it is and how it was prepared; the client's originals themselves are not offered",
          s == 200 and sorted(ga9) == ["image1-character-person.png", "image2-product-P1-front-padded.png"]
          and (gp9.get("label"), gp9.get("original_name"), gp9.get("prepared"), gp9.get("kind")) == ("Image 2 · A14 · product-front.png", "product-front.png", "padded to 9:16 on white", "product")
          and (gc9.get("label"), gc9.get("kind"), gc9.get("job")) == ("Image 1 · A28 · person.png", "character", "job-909"), str(list(ga9.values()))[:800])
    s, d, _ = req("POST", "/api/misses", {"run": "20261001-000000-07", "video": "09", "title": "The product images of a sample", "model": "wan3_0_prime",
                                          "wrong_path": str(take9), "wrong_label": "a1 · raw.mp4"})
    pm9 = d["miss"]["id"] if s == 200 else 0
    s, d, _ = req("POST", f"/api/misses/{pm9}/moments", {"frames": [{"src": "wrong", "role": "wrong", "t": 1.0}], "details": "The label is wrong."})
    mo9 = d["moment"]["moment_id"] if s == 200 else 0
    meta9 = json.loads((core.DATA / "samples" / str(mo9) / "meta.json").read_text(encoding="utf-8")) if mo9 else {}
    ps9 = meta9.get("product_samples") or []
    check("by default the band shows every image the generation was sent, each kept with its input, asset id, what it is, the client's original (by "
          "name for every reader) and how it was prepared; the sample also keeps what was sent",
          [(x.get("image"), x.get("asset"), x.get("kind"), x.get("original_name"), x.get("prepared"), x.get("label"), x.get("recorded")) for x in ps9]
          == [("Image 1", "A28", "character", "person.png", "cropped above the collar", "Image 1 · A28 · person.png", "source"),
              ("Image 2", "A14", "product", "product-front.png", "padded to 9:16 on white", "Image 2 · A14 · product-front.png", "build")]
          and [x.get("file") for x in meta9.get("images_sent") or []] == ["image1-character-person.png", "image2-product-P1-front-padded.png"]
          and meta9.get("product_sample_chosen") is False, str(meta9)[:600])
    import hashlib as _hl
    pi9 = (meta9.get("product_samples") or [{}])[-1]
    check("a sample keeps the fingerprint of each image's original (fingerprint) and of the file sent (sent_fingerprint), the first 8 hex digits of the SHA-1 of each "
          "file's content, so one photo is one file in every run whatever asset id the run gave it (the admin, 2026-11-03); a file that cannot be read has none",
          pi9.get("fingerprint") == _hl.sha1((ref9 / "product-front.png").read_bytes()).hexdigest()[:8]
          and pi9.get("sent_fingerprint") == _hl.sha1((v9 / "image2-product-P1-front-padded.png").read_bytes()).hexdigest()[:8] and pi9["fingerprint"] != pi9["sent_fingerprint"]
          and [x.get("fingerprint") for x in meta9.get("images_sent") or []] == [runs.file_fingerprint(ref9 / "person.png"), pi9["fingerprint"]]
          and runs.file_fingerprint(TMP / "no-such-file.png") == "" and runs.file_fingerprint(ref9) == "" and runs.file_fingerprint("") == "" and runs.file_fingerprint(None) == "",
          str(pi9)[:400])
    s, d, _ = req("GET", f"/api/qc/samples/S{mo9}?prompts=1", key=kq)
    smp9 = d.get("sample") or {}
    rec9 = smp9.get("record", "")
    dig9 = _pq.records_of(_pq._digest()).get(f"S{mo9}", "")
    mk_pf, mk_pe = runs.file_fingerprint(ref9 / "product-front.png"), runs.file_fingerprint(ref9 / "person.png")
    check("the judge reads which product images a sample's generation was given and how many, each by the client's original file and the fingerprint "
          "of its content: one line in the digest, one line per image with its preparation in the full record, and the same in the sample's answer",
          bool(mk_pf and mk_pe) and f"PRODUCT IMAGES OF S{mo9} (1): Image 2 · A14 · product-front.png #{mk_pf}\n" in dig9
          and f"OTHER IMAGES OF S{mo9} (1): Image 1 · A28 · person.png #{mk_pe} (character)" in dig9
          and f"  S{mo9} · Image 1 · A28 · person.png #{mk_pe}, prepared: cropped above the collar (character)" in rec9
          and f"  S{mo9} · Image 2 · A14 · product-front.png #{mk_pf}, prepared: padded to 9:16 on white" in rec9
          and smp9.get("product_images") == [{"image": "Image 2", "asset": "A14", "file": "product-front.png", "fingerprint": mk_pf, "prepared": "padded to 9:16 on white",
                                              "recorded": True}]
          and str(ref9) not in dig9 and str(ref9) not in rec9, dig9[:500] + " || " + rec9[:500])
    got9 = _pq._fulfil({"kind": "sample_origin", "id": f"S{mo9}"}, {}, TMP / "fulfil", False)
    reg9 = _pq._fulfil({"kind": "product_registry"}, {"run": "20261001-000000-07", "video": "09"}, TMP / "fulfil", False)
    check("what the judge fetches about a sample's or a run's product images names each by its asset id, the client's original file name and the "
          "fingerprint of its content",
          f"PRODUCT IMAGE 1 OF S{mo9} (product image 1, A14 · product-front.png #{mk_pf}, which the request calls " in got9.get("text", "")
          and f"product image 1 (A14 · product-front.png #{mk_pf}, which the request calls " in reg9.get("text", ""), got9.get("text", "")[-400:] + " || " + reg9.get("text", "")[-300:])
    old9 = TMP / "old-sample"
    old9.mkdir(exist_ok=True)
    (old9 / "meta.json").write_text(json.dumps({"product_samples": [{"asset": "", "label": "Image 2 · = image2-product-x-front-padded", "file": "image2-product-x-front-padded.png"}]}))
    ob9 = _pq.product_images_brief({"composite_path": str(old9 / "composite.png")})
    ow9 = _pq._pim_words(_pq.product_images({"composite_path": str(old9 / "composite.png")})[0], True)
    check("a sample saved before the originals were recorded keeps its image number and says its original file is not recorded",
          ob9 == [{"image": "Image 2", "asset": "", "file": "image2-product-x-front-padded.png", "fingerprint": "", "prepared": "", "recorded": False}]
          and ow9 == "Image 2 · = image2-product-x-front-padded · its original file is not recorded", f"{ob9} | {ow9}")
    old9b = TMP / "old-sample-b"
    old9b.mkdir(exist_ok=True)
    (old9b / "meta.json").write_text(json.dumps({"product_samples": [
        {"asset": "", "label": "Image 2 · = image2-product-x-front-padded", "file": "image2-product-x-front-padded.png", "fingerprint": "", "sent_fingerprint": "0a1b2c3d"},
        {"asset": "A14", "image": "Image 3", "label": "Image 3 · A14 · gone.png", "file": "image3-gone.png", "original": str(TMP / "gone.png"),
         "original_name": "gone.png", "fingerprint": "", "sent_fingerprint": "4e5f6a7b", "prepared": "as it is"}]}))
    nb9 = _pq.product_images_brief({"composite_path": str(old9b / "composite.png")})
    nw9 = [_pq._pim_words(x, False) for x in _pq.product_images({"composite_path": str(old9b / "composite.png")})]
    check("where the original's fingerprint cannot be had (the run did not record the original, or it cannot be read), the file sent's follows, never "
          "in the original's place",
          nw9 == ["Image 2 · = image2-product-x-front-padded · its original file is not recorded · the file sent #0a1b2c3d",
                  "Image 3 · A14 · gone.png · the file sent #4e5f6a7b"]
          and [(x["file"], x["fingerprint"]) for x in nb9] == [("image2-product-x-front-padded.png", "0a1b2c3d"), ("gone.png", "")], f"{nw9} | {nb9}")
    pif = _pq.product_images_from({"product_images": [{"asset": "the A82 front", "file": "client/assets/Carton-Front.png", "prepare": "", "why": "w"},
                                                     {"file": "C:\\x\\y\\Back.png"}, {"why": "neither"}, "not a dict"] + [{"asset": f"A{k}"} for k in range(100, 110)]})
    check("the judge's product images are held to their shape: an asset id or a file name (never its folder), the first six well-formed, in its order",
          pif[0] == {"asset": "A82", "file": "Carton-Front.png", "prepare": "", "why": "w"} and (pif[1]["asset"], pif[1]["file"]) == ("", "Back.png")
          and [x["asset"] for x in pif[2:]] == ["A100", "A101", "A102", "A103"] and len(pif) == _pq.PRODUCT_IMAGES_MAX == 6
          and _pq.product_images_from({"template": "x"}) == [] and _pq.product_images_from(None) == [], str(pif)[:500])
    pmk = _pq.product_images_from({"product_images": [{"asset": "A82", "fingerprint": "#5917FD2A"}, {"asset": "A83", "fingerprint": "A82"},
                                                     {"asset": "A84", "fingerprint": "123456789"}, {"asset": "A85"}]})
    check("the judge's product image keeps a well-formed fingerprint (8 hex digits; a # and capitals forgiven) and drops any other",
          [x.get("fingerprint") for x in pmk] == ["5917fd2a", None, None, None], str(pmk))
    check("the judge is told an asset id is the run's own number and the #fingerprint is the same file in every run, and gives each product image's fingerprint",
          "#xxxxxxxx" in _pq.LIB_HEADER and "the same for the same file in every run" in _pq.LIB_HEADER and "the file sent #xxxxxxxx" in _pq.LIB_HEADER
          and '"fingerprint":"<the 8 characters after #' in _pq.ROLE and "match a sample's images to this run's by it" in _pq.ROLE)
    check("the judge is told to give a second view where the correct versions had one: this variant's own view first, else the file that worked as a "
          "look reference whose print is not copied (the admin, 2026-11-05, any product)",
          "SECOND VIEW (the admin, 2026-11-05; any product, any brand)" in _pq.ROLE and "Look for it first among this variant's own images" in _pq.ROLE
          and "its printed design belongs to another variant and is not copied" in _pq.ROLE
          and "the run's other images of the client's products under the same registry heading" in _pq.ROLE)
    check("the judge is told a brief's claim that no sample shows does not outweigh what the correct versions used, and that a video named without "
          "its run is the brief's own run's (the admin, 2026-11-05)",
          "CLAIMS ARE NOT SAMPLES (the admin, 2026-11-05; any product)" in _pq.ROLE and "That is a claim, not evidence" in _pq.ROLE
          and "A video named without its run is a video of the brief's own run" in _pq.ROLE
          and _pq.ROLE.index("CLAIMS ARE NOT SAMPLES") < _pq.ROLE.index("SOURCE OF TRUTH"))
    check("the keeper's role is four parts, whole as before, and each call carries only the rules it needs: a QC question the QC rules, a planning or template "
          "question the planning rules (the admin, 2026-11-06)",
          _pq.ROLE == _pq.ROLE_HEAD + _pq.ROLE_QC + _pq.ROLE_PLAN + _pq.ROLE_TAIL
          and "QC QUESTION" in _pq.role_for("qc") and "PLANNING QUESTION" not in _pq.role_for("qc")
          and "PLANNING QUESTION" in _pq.role_for("template") and "QC QUESTION" not in _pq.role_for("template")
          and "SECOND VIEW" in _pq.role_for("qc") and "SECOND VIEW" in _pq.role_for("template") and _pq.rules_placement() == "system"
          and _pq.boot_rules("system") == "" and "PLANNING QUESTION" not in _pq.role_for("template", "boot") and "PLANNING QUESTION" in _pq.boot_rules("boot"),
          f"qc {len(_pq.role_for('qc'))} template {len(_pq.role_for('template'))} boot {len(_pq.role_for('template', 'boot'))}")
    stw = {"session_id": "s", "turns": 1, "chars": 0, "model": "m", "header": _pq.HEADER_SIG, "rules": "boot"}
    boot_seen = []
    real_run_b = _pq._run_claude
    _pq._run_claude = lambda prompt, sid, resume, model, **k: (boot_seen.append((prompt, k.get("role") or "")), {"result": "ready", "total_cost_usd": 0})[1]
    try:
        core.setting_set(_pq.RULES_KEY, {"planning": "boot"})
        stb, _cb = _pq._boot(1, "sig-b", "m")
    finally:
        _pq._run_claude = real_run_b
        core.setting_set(_pq.RULES_KEY, {})
    check("with the planning rules placed in the boot (python loupe.py keeper-rules boot) a fresh session is told them once after the library, the boot "
          "call's system prompt leaves them out, the session records the placement, and a session of the other placement is worn out",
          _pq.rules_placement() == "system" and stb.get("rules") == "boot" and len(boot_seen) == 1
          and "RULES FOR EVERY PLANNING QUESTION AND TEMPLATE REQUEST OF THIS SESSION" in boot_seen[0][0] and "PLANNING QUESTION (the video has NOT" in boot_seen[0][0]
          and boot_seen[0][0].rstrip().endswith("You now hold this library and these rules. Reply with the single word: ready.")
          and "PLANNING QUESTION" not in boot_seen[0][1] and "You are the Loupe keeper" in boot_seen[0][1]
          and _pq._worn(stw, "m") and not _pq._worn({**stw, "rules": "system"}, "m"), str(stb)[:200])
    tf9 = _pq.template_from({"template": "T", "product_images": [{"asset": "A14", "file": "x/product-front.png"}]}, {})
    check("a template answer carries the judge's product images", tf9.get("product_images") == [{"asset": "A14", "file": "product-front.png", "prepare": "", "why": ""}], str(tf9)[:300])
    check("the judge is told the product images are its to decide (which and how many), how they are numbered, how to name each, and where they go in a "
          "template and in a QC answer; the library header explains the records' section",
          "PRODUCT IMAGES (the admin, 2026-10-30)" in _pq.ROLE and "ride last among the image inputs" in _pq.ROLE and "<the original file's name>" in _pq.ROLE
          and '"product_images":[<see PRODUCT IMAGES; [] keeps the draft\'s>]' in _pq.ROLE
          and '"product_images":[<optional, see PRODUCT IMAGES: for the regeneration>]' in _pq.ROLE and "PRODUCT IMAGES OF S.." in _pq.LIB_HEADER)
    s, d, _ = req("PATCH", f"/api/misses/{pm9}", {"sample_files": [str(ref9 / "product-front.png")]})
    meta9b = json.loads((core.DATA / "samples" / str(mo9) / "meta.json").read_text(encoding="utf-8")) if mo9 else {}
    check("a tick on the client's original stands for the file sent from it: the report keeps the sent file, the band shows it alone",
          s == 200 and [Path(x["path"]).name for x in d["miss"]["sample_files"]] == ["image2-product-P1-front-padded.png"]
          and [x.get("file") for x in meta9b.get("product_samples") or []] == ["image2-product-P1-front-padded.png"] and len(meta9b.get("images_sent") or []) == 2
          and meta9b.get("product_sample_chosen") is True, str(meta9b)[:400])
    rq9p = atp_run / "50-requests" / "video-09-request.json"
    rq9_text = rq9p.read_text(encoding="utf-8")
    rq9p.write_text(rq9_text.replace("Video nine:", "Video nine, edited after it was sent:"), encoding="utf-8")
    changed9 = runs.sent_images("20261001-000000-07", "09", str(take9), str(take9))
    rq9p.write_text(rq9_text, encoding="utf-8")
    rq10p = atp_run / "50-requests" / "video-10-request.json"
    rq10p.write_text(json.dumps({"params": {"model": "wan3_0_prime", "prompt": "Video ten.", "medias": [
        {"role": "image_references", "value": "m10", "file": str(v9 / "image1-character-person.png")}]}}), encoding="utf-8")
    never10 = runs.sent_images("20261001-000000-07", "10")

    def ledger10(status: str):
        st10 = json.loads((atp_run / "state.json").read_text(encoding="utf-8"))
        st10["submissions"] = [x for x in st10["submissions"] if x.get("video") != "10"] + [
            {"video": "10", "lane": "10", "attempt": 1, "status": status, "job_id": "job-1010", "request_file": str(rq10p),
             "fingerprint": runs.request_fingerprint(json.loads(rq10p.read_text(encoding="utf-8")))}]
        (atp_run / "state.json").write_text(json.dumps(st10), encoding="utf-8")
        return runs.sent_images("20261001-000000-07", "10")
    failed10, rejected10 = ledger10("failed"), ledger10("rejected")
    check("nothing unverified is offered: a request changed since the ledger recorded it, one the ledger never submitted and one whose job failed (it "
          "made no video) give no image; a job QC rejected did make its video, so what it was sent is shown",
          changed9 == [] and never10 == [] and failed10 == [] and [x["name"] for x in rejected10] == ["image1-character-person.png"]
          and len(runs.sent_images("20261001-000000-07", "09", str(take9), str(take9))) == 2, f"{changed9} {never10} {failed10} {rejected10}"[:400])
    check("the band takes up to four images in a row and more in two rows; its heading says how many of the images sent with the generation it shows",
          _cmp_band.band_rows(4) == [4] and _cmp_band.band_rows(5) == [3, 2] and _cmp_band.band_rows(7) == [4, 3] and _cmp_band.band_rows(0) == []
          and _cmp_band.band_heading(3, {"images_sent": [{}, {}, {}]}) == "3 IMAGES SENT WITH THIS GENERATION"
          and _cmp_band.band_heading(2, {"images_sent": [{}, {}, {}]}) == "2 OF THE 3 IMAGES SENT WITH THIS GENERATION"
          and _cmp_band.band_heading(1, {}) == "PRODUCT SAMPLE · 1 IMAGE USED IN THIS VIDEO" and _pm.SAMPLE_FILES_MAX == 9)
    reg_rd = TMP / "registry-forms"
    (reg_rd / "30-assets").mkdir(parents=True, exist_ok=True)
    (reg_rd / "30-assets" / "registry.md").write_text("| id | type | role | files |\n|---|---|---|---|\n| NG-EF6-SPE | product | the carton | A44, A45 front-angled |\n"
                                                      "| CH12 | character | the employee | A16 |\n| P-CAN | the can | A14 |\n", encoding="utf-8")
    check("a registry's product rows are read in either form runs write (an id starting with P-, or a type cell that says product), never a character's",
          runs._registry_product_rows(reg_rd) == [("NG-EF6-SPE", ["A44", "A45"]), ("P-CAN", ["A14"])], str(runs._registry_product_rows(reg_rd)))
    prose = "- parts: " + "x" * 3600 + "." + "\n"       # an affordance sheet longer than the old 3500-character cut, before the second brand
    (reg_rd / "30-assets" / "registry.md").write_text("# Registry\n\n## Brand one\n\n| id | type | role | files |\n|---|---|---|---|\n| P-CAN | the can | A14 |\n\n" + prose
                                                      + "\n## Brand two\n\n| id | type | role | files |\n|---|---|---|---|\n| NG-EF6-SPE | product | the carton | A44, A45 front-angled |\n", encoding="utf-8")
    tbl = _pq._registry_tables(reg_rd)
    check("the registry the judge fetches holds every heading and table row and leaves the prose out, so the second brand's rows never fall beyond the cap "
          "(the admin, 2026-11-05)",
          "## Brand one" in tbl and "## Brand two" in tbl and "| NG-EF6-SPE | product | the carton | A44, A45 front-angled |" in tbl and "| P-CAN |" in tbl
          and "parts:" not in tbl and "|---|" not in tbl and len(tbl) < 400, tbl[:300])
    ai1 = _pq._fulfil({"kind": "asset_image", "id": "A14"}, {"run": "20261001-000000-07", "video": "09"}, TMP / "fulfil", True)
    ai2 = _pq._fulfil({"kind": "asset_image", "id": "#" + runs.file_fingerprint(TMP / "fulfil" / "asset-A14.png")}, {"run": "20261001-000000-07", "video": "09"}, TMP / "fulfil", True)
    ai3 = _pq._fulfil({"kind": "asset_image", "id": "A999"}, {"run": "20261001-000000-07", "video": "09"}, TMP / "fulfil", True)
    check("the judge may ask asset_image for one of the run's client images, by asset id or #fingerprint, and gets a copy to view, named by its id, file and "
          "fingerprint; an id the inventory lacks is refused (the admin, 2026-11-05)",
          ai1.get("ok") and ai2.get("ok") and not ai3.get("ok") and "the client's image A14 · product-front.png #" in ai1.get("text", "")
          and (TMP / "fulfil" / "asset-A14.png").is_file() and ai1["text"].endswith(str(TMP / "fulfil" / "asset-A14.png"))
          and "no image of the run's asset inventory is A999" in ai3.get("why_not", "")
          and "asset_image" in _pq.ROLE and "compare a candidate view with a sample" in _pq.ROLE, str(ai1)[:200] + " || " + str(ai3)[:120])
    req("DELETE", f"/api/misses/{pm9}")
    print("\n[every sample: the images its generation was sent, read from what older runs recorded (the admin, 2026-10-30: let them all have their "
          "verified used video generation assets)]")
    regg = runs._registry_products({"assets": {"products": {"geometry": {"id": "P0", "files": ["A06"]}, "excluded": [{"id": "P5", "files": ["A38"]}],
                                                            "in_rotation": [{"id": "P1", "name": "One", "canonical": "A09", "files": ["A09", "A10"]}]}}})
    check("a product registry kept in groups is read (each listed product by its id); an excluded group is left out",
          regg.get("P1") == {"name": "One", "canonical": "A09", "ids": ["A09", "A10"]} and "P5" not in regg, str(regg))
    check("a character registry is read in the forms runs wrote ({C13: {file: A17}}, {CH-M03: [A12]}); a text note gives none",
          runs._registry_characters({"assets": {"characters": {"C13": {"file": "A17", "uses": 1}, "CH-M03": ["A12"]}}}) == {"C13": ["A17"], "CH-M03": ["A12"]}
          and runs._registry_characters({"assets": {"characters": "none supplied"}}) == {})
    r8 = TMP / "atp" / "runs" / "20261001-000000-08"
    from PIL import Image as _I8

    def png8(p: Path, rgb, size=(200, 300)):
        p.parent.mkdir(parents=True, exist_ok=True)
        _I8.new("RGB", size, rgb).save(p)
        return p
    ref8 = TMP / "reference-folder-08"
    for n_, rgb in (("person.png", (200, 150, 120)), ("front.png", (220, 30, 30)), ("square.png", (30, 220, 30))):
        png8(ref8 / n_, rgb)
    (r8 / "30-assets" / "project").mkdir(parents=True, exist_ok=True)
    (r8 / "30-assets" / "project" / "asset-inventory.json").write_text(json.dumps({"items": [
        {"id": "A17", "path": str(ref8 / "person.png"), "rel": "person.png"}, {"id": "A21", "path": str(ref8 / "front.png"), "rel": "front.png"},
        {"id": "A23", "path": str(ref8 / "square.png"), "rel": "square.png"}]}))
    head8 = png8(r8 / "30-assets" / "prepared" / "C13-head.jpg", (180, 140, 110))
    front8 = png8(r8 / "30-assets" / "prepared" / "P2-front-p40.jpg", (210, 40, 40))
    png8(r8 / "50-requests" / "video-01-guide.png", (90, 90, 90))
    png8(r8 / "30-assets" / "refs" / "A21-padded.jpg", (225, 25, 25), (300, 500))
    png8(r8 / "30-assets" / "refs" / "A23-padded.jpg", (25, 225, 25), (300, 500))
    png8(r8 / "30-assets" / "project" / "previews" / "A21.jpg", (225, 25, 26), (100, 160))      # prep-assets' look previews: never sent
    png8(r8 / "30-assets" / "project" / "previews" / "A23.jpg", (25, 225, 26), (100, 160))
    png8(r8 / "30-assets" / "identity" / "id-A17.jpg", (170, 130, 100))
    can8 = png8(r8 / "30-assets" / "product" / "P2-ref.png", (200, 20, 20))
    shutil.copyfile(can8, r8 / "30-assets" / "product" / "P2-ref-copy.png")      # the same picture uploaded again for clip B, under another media id
    (r8 / "30-assets" / "uploads.json").write_text(json.dumps({"A21": "u21", "A23": "u23", "_note": "padded copies in 30-assets/refs/"}))
    (r8 / "state.json").write_text(json.dumps({"run_id": r8.name, "mode": "analyze", "model": "wan3_0_prime", "submissions": [], "assets": {
        "characters": {"C13": {"file": "A17", "uses": 1}},
        "products": {"in_rotation": [{"id": "P2", "name": "Test flavour", "canonical": "A21", "files": ["A21", "A23"]}], "excluded": [{"id": "P9", "files": ["A99"]}]},
        "uploads": {"C13-head.jpg": {"media_id": "u1", "local": str(head8), "status": "uploaded"},
                    "P2-front-p40.jpg": {"media_id": "u2", "local": str(front8), "status": "uploaded"},
                    "identity:C13": {"media_id": "u31", "file": "30-assets/identity/id-A17.jpg", "confirmed": True},
                    "P2_ref": {"id": "u32", "file": "30-assets/product/P2-ref.png", "role": "product reference"},
                    "P2_ref_b": {"id": "u36", "file": "30-assets/product/P2-ref-copy.png", "role": "product reference"}}}}))

    def req8(name: str, medias: list, job: str, ledger: bool = True, **extra):
        body = {"params": {"model": "wan3_0_prime", "duration": 5, "prompt": f"Request {name}.", "medias": medias}, **extra}
        (r8 / "50-requests").mkdir(parents=True, exist_ok=True)
        (r8 / "50-requests" / name).write_text(json.dumps(body))
        if ledger:
            s_ = json.loads((r8 / "state.json").read_text(encoding="utf-8"))
            s_["submissions"].append({"video": name.split("-")[1], "attempt": 1, "status": "completed", "job_id": job,
                                      "request_file": str(r8 / "50-requests" / name), "fingerprint": runs.request_fingerprint(body)})
            (r8 / "state.json").write_text(json.dumps(s_))

    def im8(v):
        return {"role": "image_references", "value": v}
    v01 = [{"role": "video_references", "value": "v01"}, im8("u1"), im8("u2"), im8("u3")]
    mean01 = {"Image 1": "C13-head.jpg", "Image 2": "P2-front-p40.jpg", "Image 3": "size-and-place guide (video-01-guide.png), spot and size only"}
    req8("video-01-request.json", v01, "job-801", medias_meaning=mean01)
    req8("video-01-a1-request.json", v01, "", ledger=False, medias_meaning=mean01)      # the same request kept under another name, never submitted
    take01 = r8 / "60-outputs" / "video-01" / "a1" / "raw.mp4"
    mkvideo(take01, "0x203040", "red")
    s01 = runs.sent_images(r8.name, "01", "a1 · raw.mp4", str(take01))
    check("an older run's uploads kept under `local` in its state.json, a character known by its registry code and a guide that only the request's own "
          "words name are each shown once, from the request the ledger submitted",
          [(x["label"], x["kind"], x["name"]) for x in s01] == [("Image 1 · C13-head.jpg · its original file is not recorded", "character", "C13-head.jpg"),
                                                                ("Image 2 · P2-front-p40.jpg · its original file is not recorded", "product", "P2-front-p40.jpg"),
                                                                ("Image 3 · video-01-guide.png · its original file is not recorded", "", "video-01-guide.png")]
          and {x["job"] for x in s01} == {"job-801"}, str([(x["label"], x["kind"]) for x in s01]))
    req8("video-02-request.json", [im8("u21"), im8("u23")], "job-802",
         media_map=[{"ref": "@Image1", "asset": "A21", "meaning": "front (product sheet)"}, {"ref": "@Image2", "asset": "A23", "meaning": "front, square"}])
    take02 = r8 / "60-outputs" / "video-02" / "a1" / "raw.mp4"
    mkvideo(take02, "0x203040", "green")
    s02 = runs.sent_images(r8.name, "02", "a1 · raw.mp4", str(take02))
    extra21 = png8(r8 / "30-assets" / "stage" / "A21.png", (1, 2, 3))
    s02b = runs.sent_images(r8.name, "02", "a1 · raw.mp4", str(take02))
    extra21.unlink()
    check("an uploads map that names only an asset id gives the one copy the run made of it (never its look preview); with two copies which one was sent "
          "is not known and nothing is shown; the request's media_map names each input's asset and original file",
          [(x["label"], x["kind"], x["name"]) for x in s02] == [("Image 1 · A21 · front.png", "product", "A21-padded.jpg"), ("Image 2 · A23 · square.png", "product", "A23-padded.jpg")]
          and [x["name"] for x in s02b] == ["A23-padded.jpg"], str([(x["label"], x["kind"], x["name"]) for x in s02 + s02b]))
    v3 = r8 / "60-outputs" / "video-03"
    for c_, a_, col in (("cA", "a1", "red"), ("cA", "a2", "green"), ("cB", "a1", "blue"), ("cB", "a2", "white")):
        mkvideo(v3 / c_ / a_ / "trimmed.mp4", "0x203040", col)
    joined3 = v3 / "capcut-preview.mp4"
    subprocess.run([media.find_tool("ffmpeg"), "-y", "-loglevel", "error", "-f", "lavfi", "-i", "color=c=0x203040:s=720x1280:r=30:d=6", "-pix_fmt", "yuv420p",
                    str(joined3)], capture_output=True)
    (v3 / "result.json").write_text(json.dumps({"video": "03", "jobs": [{"clip": "cA", "attempts": "2", "delivered_attempt": "1"},
                                                                       {"clip": "cB", "attempts": "2", "delivered_attempt": "1"}]}))
    req8("video-03-cA-request.json", [im8("u31"), im8("u32")], "job-8A1", medias_meaning={"Image 1": "identity C13 (A17 head crop)", "Image 2": "the product reference"})
    req8("video-03-cA-a2-request.json", [im8("u33")], "job-8A2")
    req8("video-03-cB-request.json", [im8("u31"), im8("u36")], "job-8B1")
    req8("video-03-cB-a2-request.json", [im8("u34")], "job-8B2")
    s03 = runs.sent_images(r8.name, "03", "capcut-preview.mp4", str(joined3))
    check("a take joined from clips named by letter (cA, cB) is read from its file, each clip's shipped attempt from the run's result (delivered_attempt); a "
          "picture both clips sent is one image named with both clips, with every job it was sent with",
          [(x["label"], x["kind"]) for x in s03] == [("cA, cB · Image 1 · A17 · person.png", "character"),
                                                     ("cA, cB · Image 2 · P2-ref.png · its original file is not recorded", "product")]
          and [len(x["paths"]) for x in s03] == [1, 2] and [x["jobs"] for x in s03] == [["job-8A1", "job-8B1"]] * 2,
          str([(x["label"], x["kind"], x.get("jobs")) for x in s03]))
    for a_, col in (("a1", "white"), ("a2", "yellow")):
        mkvideo(r8 / "60-outputs" / "video-04" / a_ / "raw.mp4", "0x203040", col)
    req8("video-04-request.json", [im8("u21")], "job-841")
    req8("video-04-a2-request.json", [im8("u23")], "job-842")
    other4 = r8 / "60-outputs" / "video-04" / "render.mp4"
    mkvideo(other4, "0x405060", "white")
    check("a version whose attempt cannot be told, of a video made in several attempts, gives no image (never the latest attempt's); its attempt's own "
          "file gives that attempt's",
          runs.sent_images(r8.name, "04", "render.mp4", str(other4)) == []
          and [x["name"] for x in runs.sent_images(r8.name, "04", "a2 · raw.mp4", str(r8 / "60-outputs" / "video-04" / "a2" / "raw.mp4"))] == ["A23-padded.jpg"])
    pass5 = png8(r8 / "55-references" / "video-05" / "a3" / "p2" / "image1-C13-head.jpg", (160, 120, 90))      # an attempt kept in pass folders (a3/p2)
    st5 = json.loads((r8 / "state.json").read_text(encoding="utf-8"))
    st5["assets"]["uploads"]["a3p2 Image 1"] = {"media_id": "u51", "file": str(pass5)}
    (r8 / "state.json").write_text(json.dumps(st5))
    req8("video-05-request.json", [im8("u51")], "job-851")
    take05 = r8 / "60-outputs" / "video-05" / "a1" / "raw.mp4"
    mkvideo(take05, "0x203040", "red")
    s05 = runs.sent_images(r8.name, "05", "a1 · raw.mp4", str(take05))
    check("a product's code is read in an input's own names, never in its folders (a pass folder p2 is not the product P2), as the toolkit reads it",
          [(x["name"], x["kind"]) for x in s05] == [("image1-C13-head.jpg", "character")], str([(x["name"], x["kind"]) for x in s05]))
    s, d, _ = req("POST", "/api/misses", {"run": r8.name, "video": "03", "title": "A joined take of an older run", "model": "wan3_0_prime", "wrong_path": str(joined3)})
    m8 = d["miss"]["id"] if s == 200 else 0
    s, d, _ = req("POST", f"/api/misses/{m8}/moments", {"frames": [{"src": "wrong", "role": "wrong", "t": 1.0}], "details": "The product is too big."})
    mo8 = d["moment"]["moment_id"] if s == 200 else 0
    meta8 = json.loads((core.DATA / "samples" / str(mo8) / "meta.json").read_text(encoding="utf-8")) if mo8 else {}
    s, d, _ = req("GET", f"/api/generation-assets?run={r8.name}&video=03&wrong=" + quote(str(joined3)))
    check("the sample's band shows both pictures by default, its record keeps every job each was sent with, and the picker offers each once with its copies",
          [x["label"] for x in meta8.get("images_sent") or []] == [x["label"] for x in s03] and len(meta8.get("product_samples") or []) == 2
          and (meta8.get("images_sent") or [{}, {}])[1].get("jobs") == ["job-8A1", "job-8B1"]
          and s == 200 and [len(a["paths"]) for a in d.get("assets", [])] == [1, 2] and d["assets"][1]["source"] == "sent with jobs job-8A1, job-8B1",
          str(meta8.get("images_sent"))[:400] + " | " + str(d)[:300])
    req("DELETE", f"/api/misses/{m8}")
    print("\n[a template accounts for every sample of the library (the admin, 2026-11-03)]")
    from loupe_app import qc as _qj
    libj = {f"S{i}": {"id": f"S{i}", "title": f"title {i}"} for i in range(1, 15)}
    tj = _qj.template_from({"template": "T", "basis": [{"id": f"S{i}", "worked": "w", "failed": "f", "where": "shot 1"} for i in range(1, 13)],
                            "avoided": [{"id": "S11", "mechanism": "a hand meets a rigid body", "where": "shot 2"}, {"id": "S13", "why": "an older answer's why"},
                                        {"id": "S1", "mechanism": "already in basis"}, {"id": "S99", "mechanism": "no such sample"}],
                            "set_aside": [{"id": "S12", "why": "nothing is opened"}, {"id": "S11", "why": "already avoided"}, "not a dict"]}, libj)
    check("a template answer keeps what the judge decided for every sample: basis (up to the ten whose prompts it may read), avoided (mechanism and "
          "where; an older answer's why read as its mechanism) and set aside (why), each sample once and only samples that exist, and lists those it named "
          "in none of them",
          [b["id"] for b in tj["basis"]] == [f"S{i}" for i in range(1, 11)]
          and tj["avoided"] == [{"id": "S11", "mechanism": "a hand meets a rigid body", "where": "shot 2", "title": "title 11"},
                                {"id": "S13", "mechanism": "an older answer's why", "where": "", "title": "title 13"}]
          and tj["set_aside"] == [{"id": "S12", "why": "nothing is opened", "title": "title 12"}] and tj["unjudged"] == ["S14"]
          and _qj.template_from(None, libj)["unjudged"] == [], str({k: tj[k] for k in ("avoided", "set_aside", "unjudged")})[:400])
    check("the judge is told to go through every sample of its library, read or not and whatever its product, by the same mechanism, to design each miss "
          "that applies out of its prompt and to say what it decided for each (basis, avoided, set aside)",
          "Then go through EVERY sample of your library" in _qj.ROLE and "every sample of your library stays in view for step 2" in _qj.ROLE
          and '"avoided":[{"id":"S13","mechanism":' in _qj.ROLE and '"where":"<which part of your prompt designs it out>"}],"set_aside":[{"id":"S30","why":' in _qj.ROLE
          and "every sample of your library is in exactly one of the three" in _qj.ROLE and "a sealed can opened with one finger" in _qj.ROLE)
    saved_j = (_qj._run_claude, _qj.library, _qj._prompts_text)
    try:
        _qj.library = lambda: [{"id": "S1", "prompts": "pair"}]
        _qj._prompts_text = lambda sid, s: f"PROMPTS OF {sid}"
        _qj._run_claude = lambda msg, *a, **k: {"result": json.dumps({"template": "T2", "basis": [{"id": "S1", "worked": "w2"}]}), "total_cost_usd": 0}
        first_j = {"template": "T1", "basis": [{"id": "S1"}], "avoided": [{"id": "S2", "mechanism": "m"}], "set_aside": [{"id": "S3", "why": "w"}]}
        p2j, _cj, turns_j, sent_j = _qj._prompts_round(first_j, {"session_id": "x", "model": "m"}, "basis", [], ())
    finally:
        _qj._run_claude, _qj.library, _qj._prompts_text = saved_j
    check("a template answered again after the final prompts round keeps what it decided for the other samples when its new answer leaves that out",
          p2j.get("template") == "T2" and p2j.get("avoided") == first_j["avoided"] and p2j.get("set_aside") == first_j["set_aside"] and sent_j == ["S1"],
          str(p2j)[:300])
    # the admin, 2026-11-03: a wrong example from one video and the right one from another: the images sent with each video's generation, each naming
    # its video, a picture both were sent once; a sample shows those of the videos its frames come from
    print("\n[two videos made by two generations: the images sent with each]")
    v21 = atp_run / "55-references" / "video-21"
    for _k in ("a1", "a2"):
        (v21 / _k).mkdir(parents=True, exist_ok=True)
    _PI.new("RGB", (300, 400), (0, 0, 250)).save(v21 / "a1" / "image2-product-P1-front-padded.png")
    shutil.copyfile(v21 / "a1" / "image2-product-P1-front-padded.png", v21 / "a2" / "image2-product-P1-front-padded.png")      # the same picture, another folder
    _PI.new("RGB", (300, 400), (210, 160, 130)).save(v21 / "a1" / "image1-character-man.png")
    _PI.new("RGB", (300, 400), (130, 210, 160)).save(v21 / "a2" / "image1-character-woman.png")
    _PI.new("RGB", (300, 400), (120, 200, 150)).save(ref9 / "woman.png")
    take21 = {_k: atp_run / "60-outputs" / "video-21" / _k / "raw.mp4" for _k in ("a1", "a2")}
    mkvideo(take21["a1"], "0x302050", "white")
    mkvideo(take21["a2"], "0x503020", "white")
    for _k, _rq, _who, _orig, _aid in (("a1", "video-21-request.json", "man", "person.png", "A28"), ("a2", "video-21-a2-request.json", "woman", "woman.png", "A29")):
        (atp_run / "50-requests" / _rq).write_text(json.dumps({"params": {"model": "wan3_0_prime", "duration": 5,
            "prompt": f"Video twenty-one, {_k}: the {_who} of Image 1 holds the can of Image 2.",
            "medias": [{"role": "image_references", "value": f"m21{_k}1", "source": {"original": str(ref9 / _orig), "asset_id": _aid, "kind": "character"}},
                       {"role": "image_references", "value": f"m21{_k}2", "build": f"from {ref9 / 'product-front.png'} (A14): padded to 9:16 on white"}]}}))
    (atp_run / "50-requests" / "video-21-uploads.json").write_text(json.dumps({"video": "21", "schema": 1, "uploads": {
        "a1 Image 1: man": {"media_id": "m21a11", "file": str(v21 / "a1" / "image1-character-man.png"), "role": "image_references"},
        "a1 Image 2: P1 front padded": {"media_id": "m21a12", "file": str(v21 / "a1" / "image2-product-P1-front-padded.png"), "role": "image_references"},
        "a2 Image 1: woman": {"media_id": "m21a21", "file": str(v21 / "a2" / "image1-character-woman.png"), "role": "image_references"},
        "a2 Image 2: P1 front padded": {"media_id": "m21a22", "file": str(v21 / "a2" / "image2-product-P1-front-padded.png"), "role": "image_references"}}}))
    ledger_add("video-21-request.json", "job-2101")
    ledger_add("video-21-a2-request.json", "job-2102")
    q21 = "/api/generation-assets?run=20261001-000000-07&video=21&wrong=" + quote(str(take21["a1"]))
    s, d, _ = req("GET", q21 + "&fixed=" + quote(str(take21["a2"])))
    ga21 = [(a.get("label"), a.get("videos")) for a in d.get("assets") or []]
    si21, _b21, _h21 = req("GET", ((d.get("assets") or [{}])[-1]).get("image_url", "/none"), raw=True)
    s1, d1, _ = req("GET", q21)
    check("with Video B made by another generation, the images offered are those sent with both, each naming the video it was sent with, a picture "
          "both were sent one tile (its thumbnail served); Video A alone offers its own as before",
          s == 200 and ga21 == [("Video A · Image 1 · A28 · person.png", ["A"]), ("Videos A and B · Image 2 · A14 · product-front.png", ["A", "B"]),
                                ("Video B · Image 1 · A29 · woman.png", ["B"])] and si21 == 200
          and [a.get("label") for a in d1.get("assets") or []] == ["Image 1 · A28 · person.png", "Image 2 · A14 · product-front.png"]
          and not any(a.get("videos") for a in d1.get("assets") or []), f"{ga21} | {si21} | {str(d1)[:300]}")
    s, d, _ = req("POST", "/api/misses", {"run": "20261001-000000-07", "video": "21", "title": "The right version is another generation", "model": "wan3_0_prime",
                                          "wrong_path": str(take21["a1"]), "wrong_label": "a1 · raw.mp4", "fixed_path": str(take21["a2"]), "fixed_label": "a2 · raw.mp4"})
    pm21 = d["miss"]["id"] if s == 200 else 0
    s, d, _ = req("POST", f"/api/misses/{pm21}/moments", {"frames": [{"src": "wrong", "role": "wrong", "t": 1.0}, {"src": "fixed", "role": "right", "t": 1.0}],
                                                          "details": "The man holds the can upside down; the woman holds it upright."})
    mo21 = d["moment"]["moment_id"] if s == 200 else 0
    s, d, _ = req("POST", f"/api/misses/{pm21}/moments", {"frames": [{"src": "fixed", "role": "right", "t": 2.0}], "details": "The right version alone."})
    mo21b = d["moment"]["moment_id"] if s == 200 else 0

    def m21(mo):
        return json.loads((core.DATA / "samples" / str(mo) / "meta.json").read_text(encoding="utf-8")) if mo else {}
    meta21, meta21b = m21(mo21), m21(mo21b)
    check("a sample of both videos' frames shows the images sent with each, each named by its video, under a heading that says so; a sample of "
          "Video B's frames alone shows Video B's, as one generation",
          [x.get("label") for x in meta21.get("product_samples") or []] == ["Video A · Image 1 · A28 · person.png",
                                                                          "Videos A and B · Image 2 · A14 · product-front.png", "Video B · Image 1 · A29 · woman.png"]
          and [x.get("videos") for x in meta21.get("images_sent") or []] == [["A"], ["A", "B"], ["B"]] and meta21.get("images_videos") == ["A", "B"]
          and _cmp_band.band_heading(3, meta21) == "3 IMAGES SENT WITH THE GENERATIONS OF VIDEO A AND VIDEO B"
          and _cmp_band.band_heading(2, meta21) == "2 OF THE 3 IMAGES SENT WITH THE GENERATIONS OF VIDEO A AND VIDEO B"
          and [x.get("label") for x in meta21b.get("product_samples") or []] == ["Image 1 · A29 · woman.png", "Image 2 · A14 · product-front.png"]
          and "images_videos" not in meta21b and not any(x.get("videos") for x in meta21b.get("images_sent") or []),
          f"{meta21.get('product_samples')} || {meta21b.get('product_samples')}"[:900])
    s, d, _ = req("GET", f"/api/qc/samples/S{mo21}?prompts=1", key=kq)
    smp21 = d.get("sample") or {}
    dig21 = _pq.records_of(_pq._digest()).get(f"S{mo21}", "")
    mk_f21, mk_p21, mk_w21 = (runs.file_fingerprint(ref9 / _n) for _n in ("product-front.png", "person.png", "woman.png"))
    check("the judge reads which video's generation each image was sent with (Video A, Video B, or Videos A and B for one sent with both) in the "
          "digest, the full record and the sample's answer, and the library header explains it",
          f"PRODUCT IMAGES OF S{mo21} (1): Videos A and B · Image 2 · A14 · product-front.png #{mk_f21}\n" in dig21
          and f"OTHER IMAGES OF S{mo21} (2): Video A · Image 1 · A28 · person.png #{mk_p21} (character); Video B · Image 1 · A29 · woman.png #{mk_w21} (character)" in dig21
          and "the generations of its two videos were given, each by the video it was sent with" in smp21.get("record", "")
          and f"  S{mo21} · Video B · Image 1 · A29 · woman.png #{mk_w21} (character)" in smp21.get("record", "")
          and smp21.get("product_images") == [{"image": "Image 2", "asset": "A14", "file": "product-front.png", "fingerprint": mk_f21,
                                               "prepared": "padded to 9:16 on white", "recorded": True, "video": "A and B"}]
          and "Videos A and B for one sent with both" in _pq.LIB_HEADER, dig21[:700] + " || " + smp21.get("record", "")[:700])
    got21 = _pq._fulfil({"kind": "sample_origin", "id": f"S{mo21}"}, {}, TMP / "fulfil", False)
    check("what the judge fetches about a sample of two videos hands it the product images its generations were sent, from the client's originals, "
          "each with the video it was sent with",
          f"PRODUCT IMAGE 1 OF S{mo21} (product image 1, A14 · product-front.png #{mk_f21}, which the request calls Image 2, sent with Videos A and B): "
          in got21.get("text", "") and "PRODUCT IMAGE 2 OF" not in got21.get("text", ""), got21.get("text", "")[-600:])
    s, d, _ = req("PATCH", f"/api/misses/{pm21}", {"sample_files": [str(v21 / "a2" / "image1-character-woman.png")]})
    meta21c, meta21d = m21(mo21), m21(mo21b)
    s2, d2, _ = req("PATCH", f"/api/misses/{pm21}", {"sample_files": [str(v21 / "a1" / "image1-character-man.png")]})
    meta21e = m21(mo21b)
    s3, d3, _ = req("PATCH", f"/api/misses/{pm21}", {"sample_files": [str(ref9 / "person.png"), str(TMP / "elsewhere.png")]})
    check("the admin's ticks choose among both videos' images: one sent with Video B is accepted and each sample shows it where its frames' videos were "
          "sent it; one ticked from Video A alone leaves the band of a sample of Video B's frames empty, never its whole set; any other file is refused",
          s == 200 and [x.get("label") for x in meta21c.get("product_samples") or []] == ["Video B · Image 1 · A29 · woman.png"]
          and meta21c.get("product_sample_chosen") is True and [x.get("label") for x in meta21d.get("product_samples") or []] == ["Image 1 · A29 · woman.png"]
          and s2 == 200 and not meta21e.get("product_samples") and meta21e.get("product_sample_chosen") is True
          and s3 == 400 and "not one of the images the generations of this report's videos were sent" in str(d3),
          f"{s} {str(d)[:200]} | {meta21c.get('product_samples')} | {s2} {meta21e.get('product_samples')} | {s3} {str(d3)[:200]}"[:900])
    clean21 = take21["a1"].with_name("clean.mp4")
    shutil.copyfile(take21["a1"], clean21)
    one21 = runs.videos_sent_images("20261001-000000-07", "21", [("A", str(take21["a1"]), str(take21["a1"])), ("B", str(clean21), str(clean21))])
    check("two files of one generation (a take and a copy made from it) read as that one generation: its images, named as before",
          one21["two"] is False and [x["label"] for x in one21["images"]] == ["Image 1 · A28 · person.png", "Image 2 · A14 · product-front.png"], str(one21)[:500])
    v22 = atp_run / "55-references" / "video-22" / "a1"
    v22.mkdir(parents=True, exist_ok=True)
    _PI.new("RGB", (300, 400), (90, 90, 200)).save(v22 / "image1-product-P1-front-padded.png")
    take22 = atp_run / "60-outputs" / "video-22" / "a1" / "raw.mp4"
    mkvideo(take22, "0x203050", "white")
    (atp_run / "50-requests" / "video-22-request.json").write_text(json.dumps({"params": {"model": "wan3_0_prime", "duration": 5,
        "prompt": "Video twenty-two: the can of Image 1.",
        "medias": [{"role": "image_references", "value": "m221", "build": f"from {ref9 / 'product-front.png'} (A14): padded to 9:16 on white"}]}}))
    home_rel = f"runs/{atp_run.name}/55-references/video-22/a1/image1-product-P1-front-padded.png"      # as an agent working from the pipeline's home wrote it
    (atp_run / "50-requests" / "video-22-uploads.json").write_text(json.dumps({"video": "22", "schema": 1, "uploads": {
        "a1 Image 1: P1 front padded": {"media_id": "m221", "file": home_rel, "role": "image_references"}}}))
    ledger_add("video-22-request.json", "job-2201")
    s22 = runs.sent_images("20261001-000000-07", "22", str(take22), path=str(take22))
    pf22 = v22 / "image1-product-P1-front-padded.png"
    check("a file an uploads record names relative to the pipeline's home (runs/<run>/...) is found in the run, as one named inside the run or in full",
          [x["name"] for x in s22] == ["image1-product-P1-front-padded.png"] and runs.run_file(atp_run, home_rel) == pf22
          and runs.run_file(atp_run, "55-references/video-22/a1/image1-product-P1-front-padded.png") == pf22 and runs.run_file(atp_run, str(pf22)) == pf22
          and runs.run_file(atp_run, "") is None and runs.run_file(atp_run, "nowhere/x.png") == atp_run / "nowhere" / "x.png", str(s22)[:300])
    f15 = _cmp_band.font(15, True)
    check("a character the picture's font cannot draw (a client's file named with a sun emoji) is written as its name, never drawn as a box; one it can "
          "draw stays",
          _cmp_band._drawable("🌞.jpg", f15) == "[sun with face].jpg" and _cmp_band._drawable("Café · “x”", f15) == "Café · “x”"
          and _cmp_band._drawable("download (18).jpg", f15) == "download (18).jpg", ascii(_cmp_band._drawable("🌞.jpg", f15)))
    req("DELETE", f"/api/misses/{pm21}")
    # the admin, 2026-11-03: a request written with the attempt last (video-06-request-a2.json) is the take a2's own, read like video-06-a2-request.json
    rq_n = TMP / "attempt-last" / "50-requests"
    rq_n.mkdir(parents=True, exist_ok=True)
    for _n, _p in (("video-06-request.json", "first"), ("video-06-request-a2.json", "second"), ("video-06-request-a2.pre-product-images.json", "kept copy"),
                   ("video-04-c1-request.json", "c1 first"), ("video-04-c1-request-a2.json", "c1 second")):
        (rq_n / _n).write_text(json.dumps({"params": {"model": "wan3_0_prime", "duration": 5, "prompt": _p}}))
    rd_n = rq_n.parent
    got_n = ([f.name for f in runs.video_request_files(rd_n, "06")], [f.name for f in runs._request_files(rd_n, "06", 2)][:1],
             [f.name for f in runs._request_files(rd_n, "06", 1)][:1], runs._attempt_in_name("video-06-request-a2.json"),
             runs._request_tag("video-04-c1-request-a2.json", "04"), runs.request_canon("video-04-c1-request-a2.json"))
    check("a request named with the attempt last (video-06-request-a2.json, video-04-c1-request-a2.json) is the take a2's own, read like "
          "video-06-a2-request.json; a kept copy is not a request",
          got_n == (["video-06-request-a2.json", "video-06-request.json"], ["video-06-request-a2.json"], ["video-06-request.json"], 2, "c1-a2",
                    "video-04-c1-a2-request.json"), str(got_n))
    print("\n[cleanup]")
    httpd.shutdown()
    shutil.rmtree(TMP, ignore_errors=True)
    print(f"\n{len(OK)} passed, {len(BAD)} failed")
    if BAD:
        print("FAILED:", ", ".join(BAD))
    sys.exit(1 if BAD else 0)


if __name__ == "__main__":
    main("--keeper" in sys.argv)
