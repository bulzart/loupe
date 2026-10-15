"""Loupe misses: the admin reports a miss on a video, marks moments on it, and Loupe builds the wrong-vs-correct sample image."""
from __future__ import annotations

import json
import re
import shutil
from pathlib import Path

from PIL import Image

from . import compose, core, media, runs

KINDS = ["counts", "openings-use", "preparation", "persistent-state", "permanence", "liquids", "scale-size", "body-mechanics", "label-orientation",
         "hand-off", "environment", "timing", "stylised", "solidity", "blend-paste", "posture-place", "identity", "text-on-screen", "audio-sync",
         "product-missing", "continuity", "other"]


def _kinds(v) -> str:
    items = v if isinstance(v, list) else str(v or "").replace(";", ",").split(",")
    out = []
    for k in items:
        k = str(k).strip().lower().replace(" ", "-")
        k = core.safe_name(k, 40) if k else ""
        if k and k not in out:
            out.append(k)
    return ",".join(out[:8])


# A moment is any number of frames the admin chose, none at all included (a scenario in words only).  Each frame comes from one of the report's two videos (src "wrong" =
# the version as generated, "fixed" = the right version), has its own time, and is called WRONG, RIGHT or neither (role "neutral") by the admin; the marks belong to a
# frame (its index).  An older moment holds no frames column at all: it is the miss frame at t_wrong and, when the report has a right version, the right frame at t_fixed.
FRAME_DESC = {"wrong": "as generated", "fixed": "correct version"}
TITLE_MAX = 2000       # the title is often a whole paragraph and is burned into the image in full: never cut it short


def _frames_clean(raw, miss: dict) -> list[dict]:
    out = []
    for f in (raw or [])[:compose.MAX_FRAMES]:
        if not isinstance(f, dict):
            continue
        src = "fixed" if f.get("src") == "fixed" else "wrong"
        role = {"right": "right", "neutral": "neutral", "none": "neutral"}.get(f.get("role"), "wrong")
        try:
            t = max(0.0, float(f.get("t") or 0.0))
        except (TypeError, ValueError):
            t = 0.0
        if src == "fixed" and not miss.get("fixed_path"):
            raise ValueError("a frame comes from the right version, but this report has none")
        if src == "wrong" and not miss.get("wrong_path"):
            raise ValueError("a frame comes from Video A, but this report has none")
        out.append({"src": src, "role": role, "t": round(t, 3)})
    return out


def _frames_legacy(tw, tf, miss: dict) -> list[dict]:
    tw = max(0.0, float(tw or 0.0))
    fr = [{"src": "wrong", "role": "wrong", "t": round(tw, 3)}] if miss.get("wrong_path") else []
    if miss.get("fixed_path"):
        fr.append({"src": "fixed", "role": "right", "t": round(max(0.0, float(tf if tf not in (None, "") else tw)), 3)})
    return fr


def frames_of(row, miss: dict) -> list[dict]:
    try:
        raw = row["frames"]
    except (KeyError, IndexError):
        raw = None
    if raw not in (None, ""):          # saved with frames, possibly none: that is a scenario in words only, not an older moment
        try:
            return [{"src": f["src"], "role": f["role"], "t": f["t"]} for f in json.loads(raw) if isinstance(f, dict) and "t" in f]
        except (TypeError, ValueError):
            pass
    return _frames_legacy(row["t_wrong"], row["t_fixed"], miss)


def _times(frames: list[dict]) -> tuple[float, float]:
    """The two summary times kept for listings: the first miss frame and the first right frame."""
    if not frames:
        return 0.0, 0.0
    tw = next((f["t"] for f in frames if f["role"] == "wrong"), frames[0]["t"])
    return tw, next((f["t"] for f in frames if f["role"] == "right"), tw)


def _fit_shapes(shapes: list[dict], keep: list[int]) -> list[dict]:
    """The marks of the frames that stay, renumbered to the new frame order."""
    return [dict(s, frame=keep.index(s["frame"])) for s in shapes if s["frame"] in keep]


def _write_frames(mid: int, frames: list[dict], shapes: list[dict]):
    tw, tf = _times(frames)
    con = core.db()
    try:
        con.execute("UPDATE moments SET frames=?, shapes=?, t_wrong=?, t_fixed=?, updated=? WHERE id=?", (core.jdump(frames), core.jdump(shapes), tw, tf, core.now(), mid))
        con.commit()
    finally:
        con.close()


def sample_dir(mid: int) -> Path:
    return core.DATA / "samples" / str(int(mid))


# ------------------------------------------------------------------ misses
def create(data: dict, actor: str) -> dict:
    wrong, fixed = str(data.get("wrong_path") or ""), str(data.get("fixed_path") or "")
    if wrong:
        media.resolve_media(wrong)      # raises when the file is missing or not readable
    if fixed:
        media.resolve_media(fixed)      # neither video is required: a report can be a scenario in words only (the idea, what could be better, what the agents missed)
    if not str(data.get("title") or "").strip():
        raise ValueError("a title is needed (one line: what went wrong)")
    con = core.db()
    try:
        cur = con.execute("""INSERT INTO misses(created,updated,run,video,title,explanation,kinds,model,mode,product,status,wrong_path,wrong_label,
                             fixed_path,fixed_label,sample_on) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                          (core.now(), core.now(), str(data.get("run") or ""), core.nn(data.get("video")), str(data["title"]).strip()[:TITLE_MAX],
                           str(data.get("explanation") or "").strip(), _kinds(data.get("kinds")), str(data.get("model") or ""),
                           str(data.get("mode") or ""), str(data.get("product") or ""), "active", wrong, str(data.get("wrong_label") or ""),
                           fixed, str(data.get("fixed_label") or ""), 0 if data.get("product_sample") is False else 1))
        con.commit()
        mid = cur.lastrowid
    finally:
        con.close()
    core.audit(actor, "miss.create", f"miss {mid}", data.get("title", ""))
    return get(mid)


def _miss_row(r) -> dict:
    d = dict(r)
    d["kinds"] = [k for k in (d.get("kinds") or "").split(",") if k]
    d["product_sample"] = d.get("sample_on") != 0       # the product sample is shown in the saved image unless the admin turned it off
    return d


def get(mid: int) -> dict | None:
    con = core.db()
    try:
        r = con.execute("SELECT * FROM misses WHERE id=?", (mid,)).fetchone()
        if not r:
            return None
        d = _miss_row(r)
        d["moments"] = [moment_view(m, d) for m in con.execute("SELECT * FROM moments WHERE miss_id=? ORDER BY t_wrong, id", (mid,))]
        return d
    finally:
        con.close()


def listing(run: str = "", status: str = "") -> list[dict]:
    con = core.db()
    try:
        sql, args = "SELECT * FROM misses", []
        cond = []
        if run:
            cond.append("run=?"); args.append(run)
        if status:
            cond.append("status=?"); args.append(status)
        if cond:
            sql += " WHERE " + " AND ".join(cond)
        out = []
        for r in con.execute(sql + " ORDER BY id DESC", args):
            d = _miss_row(r)
            ms = con.execute("SELECT * FROM moments WHERE miss_id=? ORDER BY t_wrong, id", (r["id"],)).fetchall()
            d["moments"] = [moment_view(m, d) for m in ms]
            out.append(d)
        return out
    finally:
        con.close()


def update(mid: int, patch: dict, actor: str) -> dict | None:
    cur = get(mid)
    if not cur:
        return None
    cols = {}
    for k in ("title", "explanation", "model", "mode", "product", "status", "run", "wrong_label", "fixed_label"):
        if k in patch:
            cols[k] = str(patch[k] or "")
    if "video" in patch:
        cols["video"] = core.nn(patch["video"])
    if "product_sample" in patch:
        cols["sample_on"] = 0 if patch["product_sample"] in (False, 0, "0", "false") else 1
    if "kinds" in patch:
        cols["kinds"] = _kinds(patch["kinds"])
    for k in ("wrong_path", "fixed_path"):
        if k in patch and (patch[k] or "") != cur[k]:
            v = str(patch[k] or "")
            if v:       # either video can be cleared (a miss example, or a scenario in words only) or attached later
                media.resolve_media(v)
            cols[k] = v
    if cols:
        cols["updated"] = core.now()
        con = core.db()
        try:
            con.execute("UPDATE misses SET " + ",".join(f"{k}=?" for k in cols) + " WHERE id=?", list(cols.values()) + [mid])
            con.commit()
        finally:
            con.close()
        core.audit(actor, "miss.update", f"miss {mid}", ",".join(cols))
        changed = [src for src, k in (("wrong", "wrong_path"), ("fixed", "fixed_path")) if k in cols and cols[k] != (cur[k] or "")]
        if changed:
            for mo in cur["moments"]:       # a right version attached later adds the right frame at the same second; clearing a video drops the frames that came from it
                fr, sh = [{k: f[k] for k in ("src", "role", "t")} for f in mo["frames"]], compose.clean_shapes(mo["shapes"])
                drop = [src for src in changed if not cols["wrong_path" if src == "wrong" else "fixed_path"]]
                if drop:
                    keep = [i for i, f in enumerate(fr) if f["src"] not in drop]
                    fr, sh = [fr[i] for i in keep], _fit_shapes(sh, keep)
                elif "fixed" in changed and not cur["fixed_path"] and len(fr) == 1 and fr[0]["role"] == "wrong" and fr[0]["src"] == "wrong":
                    fr.append({"src": "fixed", "role": "right", "t": fr[0]["t"]})
                _write_frames(mo["moment_id"], fr, sh)
        if any(k in cols for k in ("title", "explanation", "wrong_path", "fixed_path", "model", "run", "video", "sample_on", "wrong_label")):
            for m in cur["moments"]:       # the sample image carries the title, the caption and the source of both frames
                regenerate(m["moment_id"])
    return get(mid)


def delete(mid: int, actor: str):
    con = core.db()
    try:
        ids = [r["id"] for r in con.execute("SELECT id FROM moments WHERE miss_id=?", (mid,))]
        con.execute("DELETE FROM moments WHERE miss_id=?", (mid,))
        con.execute("DELETE FROM misses WHERE id=?", (mid,))
        con.commit()
    finally:
        con.close()
    for i in ids:
        shutil.rmtree(sample_dir(i), ignore_errors=True)
    core.audit(actor, "miss.delete", f"miss {mid}")


# ------------------------------------------------------------------ moments
def add_moment(miss_id: int, data: dict, actor: str) -> dict:
    m = get(miss_id)
    if not m:
        raise LookupError("no such miss")
    frames = _frames_clean(data["frames"], m) if data.get("frames") is not None else _frames_legacy(data.get("t_wrong"), data.get("t_fixed"), m)
    tw, tf = _times(frames)
    shapes = _fit_shapes(compose.clean_shapes(data.get("shapes")), list(range(len(frames))))
    con = core.db()
    try:
        cur = con.execute("""INSERT INTO moments(miss_id,created,updated,t_wrong,t_fixed,details,correct_note,shapes,kinds,status,frames,idea,better,missed)
                             VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                          (miss_id, core.now(), core.now(), tw, tf, str(data.get("details") or "").strip(),
                           str(data.get("correct_note") or "").strip(), core.jdump(shapes), _kinds(data.get("kinds")), "active", core.jdump(frames),
                           str(data.get("idea") or "").strip(), str(data.get("better") or "").strip(), str(data.get("missed") or "").strip()))
        con.commit()
        mid = cur.lastrowid
    finally:
        con.close()
    first = str(data.get("details") or "").strip()
    if not (m["explanation"] or "").strip() and first:     # the report's summary defaults to its first explanation
        con = core.db()
        try:
            con.execute("UPDATE misses SET explanation=?, updated=? WHERE id=?", (first, core.now(), miss_id))
            con.commit()
        finally:
            con.close()
    regenerate(mid)
    core.audit(actor, "moment.add", f"moment {mid}", f"miss {miss_id}: " + ", ".join(f"{f['role']} @ {f['t']:.2f}s" for f in frames))
    return moment(mid)


def update_moment(mid: int, patch: dict, actor: str) -> dict | None:
    cur = moment(mid)
    if not cur:
        return None
    cols = {}
    frames = None
    if patch.get("frames") is not None:
        frames = _frames_clean(patch["frames"], {"fixed_path": cur["has_right_version"], "wrong_path": cur["has_wrong_version"]})
    elif "t_wrong" in patch or "t_fixed" in patch:       # an older caller moves the first miss frame and the first right frame
        frames = [dict(f) for f in cur["frames"]]
        for key, role in (("t_wrong", "wrong"), ("t_fixed", "right")):
            if key in patch:
                f = next((f for f in frames if f["role"] == role), None)
                if f:
                    f["t"] = round(max(0.0, float(patch[key])), 3)
    if frames is not None:
        cols["frames"] = core.jdump(frames)
        cols["t_wrong"], cols["t_fixed"] = _times(frames)
    for k in ("details", "correct_note", "status", "idea", "better", "missed"):
        if k in patch:
            cols[k] = str(patch[k] or "").strip()
    if "shapes" in patch or frames is not None:
        nfr = len(frames if frames is not None else cur["frames"])
        cols["shapes"] = core.jdump(_fit_shapes(compose.clean_shapes(patch["shapes"] if "shapes" in patch else cur["shapes"]), list(range(nfr))))
    if "kinds" in patch:
        cols["kinds"] = _kinds(patch["kinds"])
    if cols:
        cols["updated"] = core.now()
        con = core.db()
        try:
            con.execute("UPDATE moments SET " + ",".join(f"{k}=?" for k in cols) + " WHERE id=?", list(cols.values()) + [mid])
            con.commit()
        finally:
            con.close()
        core.audit(actor, "moment.update", f"moment {mid}", ",".join(cols))
        if any(k in cols for k in ("frames", "t_wrong", "t_fixed", "details", "correct_note", "shapes", "idea", "better", "missed")):
            regenerate(mid)
    return moment(mid)


def delete_moment(mid: int, actor: str):
    con = core.db()
    try:
        con.execute("DELETE FROM moments WHERE id=?", (mid,))
        con.commit()
    finally:
        con.close()
    shutil.rmtree(sample_dir(mid), ignore_errors=True)
    core.audit(actor, "moment.delete", f"moment {mid}")


def regenerate(mid: int):
    """Cut every frame of the moment, draw the admin's marks on each, build the composite and the zoom, and store them."""
    con = core.db()
    try:
        mr = con.execute("SELECT * FROM moments WHERE id=?", (mid,)).fetchone()
        miss = con.execute("SELECT * FROM misses WHERE id=?", (mr["miss_id"],)).fetchone()
    finally:
        con.close()
    frames = frames_of(mr, dict(miss))
    shapes = compose.clean_shapes(json.loads(mr["shapes"] or "[]"))
    items, pngs = [], []
    for i, f in enumerate(frames):
        path = miss["fixed_path"] if f["src"] == "fixed" else miss["wrong_path"]
        if not path:             # its video was cleared from the report
            continue
        png = media.frame_file(media.resolve_media(path), f["t"])
        items.append({"img": Image.open(png).convert("RGB"), "role": f["role"], "desc": FRAME_DESC[f["src"]], "t": f["t"], "shapes": [s for s in shapes if s["frame"] == i]})
        pngs.append(png)
    tw, tf = _times(frames)
    meta = {"title": miss["title"], "model": miss["model"], "run": miss["run"], "video": miss["video"], "t_wrong": tw, "t_fixed": tf, "sample": f"S{mid}",
            "frames": [{"n": k + 1, "role": it["role"], "from": it["desc"], "t": it["t"]} for k, it in enumerate(items)]}
    samples = []
    if miss["sample_on"] != 0:
        try:       # the product image(s) the video used, straight from the client's reference folder; never a reason to fail the save
            for ps in runs.product_samples(miss["run"], miss["video"], miss["wrong_label"] or miss["wrong_path"]):
                samples.append((compose.load_product(ps["path"]), ps["label"]))
                meta.setdefault("product_samples", []).append({"asset": ps["asset_id"], "label": ps["label"], "file": ps["name"]})
        except Exception as e:  # noqa: BLE001
            core.audit("system", "product-sample.failed", f"miss {miss['id']}", str(e)[:300])
            samples = []
    comp, zoom, ann = compose.build(items, meta, mr["details"] or miss["explanation"] or "", mr["correct_note"] or "", product_samples=samples,
                                    idea=mr["idea"] or "", better=mr["better"] or "", missed=mr["missed"] or "")
    d = sample_dir(mid)
    d.mkdir(parents=True, exist_ok=True)
    for old in list(d.glob("frame-*.png")) + [d / n for n in ("wrong.png", "annotated.png", "fixed.png", "annotated_fixed.png", "zoom.png")]:
        if old.exists():
            old.unlink()
    for k, (png, a_img) in enumerate(zip(pngs, ann), 1):          # every frame, plain and marked
        shutil.copyfile(png, d / f"frame-{k}.png")
        a_img.save(d / f"frame-{k}-marked.png")
    for role, plain, marked in (("wrong", "wrong.png", "annotated.png"), ("right", "fixed.png", "annotated_fixed.png")):      # the first of each role keeps its older name
        k = next((k for k, it in enumerate(items) if it["role"] == role), None)
        if k is not None:
            shutil.copyfile(pngs[k], d / plain)
            ann[k].save(d / marked)
    comp.save(d / "composite.png", optimize=True)
    if zoom:
        zoom.save(d / "zoom.png", optimize=True)
    con = core.db()
    try:
        con.execute("UPDATE moments SET w=?, h=?, updated=? WHERE id=?", (items[0]["img"].width if items else None, items[0]["img"].height if items else None, core.now(), mid))
        con.commit()
    finally:
        con.close()
    (d / "meta.json").write_text(json.dumps({"moment": mid, "miss": miss["id"], **meta, "shapes": shapes}, ensure_ascii=False, indent=1), encoding="utf-8")


def moment_file(mid: int, kind: str) -> Path | None:
    name = {"composite": "composite.png", "zoom": "zoom.png", "wrong": "wrong.png", "fixed": "fixed.png", "annotated": "annotated.png", "annotated_fixed": "annotated_fixed.png"}.get(kind)
    if not name and re.fullmatch(r"frame-\d{1,2}(-marked)?", kind or ""):       # frame-3 (plain) or frame-3-marked
        name = kind + ".png"
    p = sample_dir(mid) / (name or "x")
    return p if name and p.is_file() else None


def moment_view(r, miss: dict) -> dict:
    mid = r["id"]
    kinds = [k for k in (r["kinds"] or "").split(",") if k] or miss.get("kinds", [])
    d = sample_dir(mid)
    frames = frames_of(r, miss)
    has_wrong, has_right = any(f["role"] == "wrong" for f in frames), any(f["role"] == "right" for f in frames)
    return {"moment_id": mid, "frames": [dict(f, desc=FRAME_DESC[f["src"]]) for f in frames], "n_frames": len(frames), "has_right_version": miss.get("fixed_path") or "",
            "has_wrong_version": miss.get("wrong_path") or "", "has_wrong": has_wrong, "sample_kind": ("pair" if has_right else "miss-only") if has_wrong else "scenario",
            "idea": r["idea"] or "", "better": r["better"] or "", "missed": r["missed"] or "", "id": f"S{mid}", "miss_id": miss["id"], "title": miss["title"], "explanation": miss["explanation"], "details": r["details"],
            "correct_note": r["correct_note"], "kinds": kinds, "model": miss["model"], "mode": miss["mode"], "product": miss["product"],
            "run": miss["run"], "video": miss["video"], "t_wrong": r["t_wrong"], "t_fixed": r["t_fixed"], "shapes": compose.clean_shapes(json.loads(r["shapes"] or "[]")),
            "status": r["status"], "miss_status": miss["status"], "created": r["created"], "updated": r["updated"],
            "has_right": has_right, "image_url": f"/api/moments/{mid}/image?kind=composite", "zoom_url": f"/api/moments/{mid}/image?kind=zoom" if (d / "zoom.png").exists() else "",
            "composite_path": str(d / "composite.png")}


def moment(mid: int) -> dict | None:
    con = core.db()
    try:
        r = con.execute("SELECT * FROM moments WHERE id=?", (mid,)).fetchone()
        if not r:
            return None
        miss = get(r["miss_id"])
        return moment_view(r, {k: v for k, v in miss.items() if k != "moments"})
    finally:
        con.close()
