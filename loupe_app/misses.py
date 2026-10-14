"""Loupe misses: the admin reports a miss on a video, marks moments on it, and Loupe builds the wrong-vs-correct sample image."""
from __future__ import annotations

import json
import shutil
from pathlib import Path

from PIL import Image

from . import compose, core, media

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


def sample_dir(mid: int) -> Path:
    return core.DATA / "samples" / str(int(mid))


# ------------------------------------------------------------------ misses
def create(data: dict, actor: str) -> dict:
    wrong, fixed = str(data.get("wrong_path") or ""), str(data.get("fixed_path") or "")
    media.resolve_media(wrong)      # raises when the file is missing or not readable
    if fixed:
        media.resolve_media(fixed)      # a miss may have no right version at all: it then stays as a miss example
    if not str(data.get("title") or "").strip():
        raise ValueError("a title is needed (one line: what went wrong)")
    con = core.db()
    try:
        cur = con.execute("""INSERT INTO misses(created,updated,run,video,title,explanation,kinds,model,mode,product,status,wrong_path,wrong_label,
                             fixed_path,fixed_label) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                          (core.now(), core.now(), str(data.get("run") or ""), core.nn(data.get("video")), str(data["title"]).strip()[:200],
                           str(data.get("explanation") or "").strip(), _kinds(data.get("kinds")), str(data.get("model") or ""),
                           str(data.get("mode") or ""), str(data.get("product") or ""), "active", wrong, str(data.get("wrong_label") or ""),
                           fixed, str(data.get("fixed_label") or "")))
        con.commit()
        mid = cur.lastrowid
    finally:
        con.close()
    core.audit(actor, "miss.create", f"miss {mid}", data.get("title", ""))
    return get(mid)


def _miss_row(r) -> dict:
    d = dict(r)
    d["kinds"] = [k for k in (d.get("kinds") or "").split(",") if k]
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
    if "kinds" in patch:
        cols["kinds"] = _kinds(patch["kinds"])
    for k in ("wrong_path", "fixed_path"):
        if k in patch and (patch[k] or "") != cur[k]:
            v = str(patch[k] or "")
            if v or k == "wrong_path":       # the right version can be cleared (a miss example again) or attached later; the wrong one is required
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
        if any(k in cols for k in ("title", "explanation", "wrong_path", "fixed_path", "model", "run", "video")):
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
    tw = float(data.get("t_wrong") or 0.0)
    tf = float(data.get("t_fixed") if data.get("t_fixed") not in (None, "") else tw)
    shapes = compose.clean_shapes(data.get("shapes"))
    con = core.db()
    try:
        cur = con.execute("""INSERT INTO moments(miss_id,created,updated,t_wrong,t_fixed,details,correct_note,shapes,kinds,status)
                             VALUES(?,?,?,?,?,?,?,?,?,?)""",
                          (miss_id, core.now(), core.now(), tw, tf, str(data.get("details") or "").strip(),
                           str(data.get("correct_note") or "").strip(), core.jdump(shapes), _kinds(data.get("kinds")), "active"))
        con.commit()
        mid = cur.lastrowid
    finally:
        con.close()
    if not (m["explanation"] or "").strip() and str(data.get("details") or "").strip():     # the report's summary defaults to its first explanation
        con = core.db()
        try:
            con.execute("UPDATE misses SET explanation=?, updated=? WHERE id=?", (str(data["details"]).strip(), core.now(), miss_id))
            con.commit()
        finally:
            con.close()
    regenerate(mid)
    core.audit(actor, "moment.add", f"moment {mid}", f"miss {miss_id} @ {tw:.2f}s")
    return moment(mid)


def update_moment(mid: int, patch: dict, actor: str) -> dict | None:
    cur = moment(mid)
    if not cur:
        return None
    cols = {}
    for k in ("t_wrong", "t_fixed"):
        if k in patch:
            cols[k] = float(patch[k])
    for k in ("details", "correct_note", "status"):
        if k in patch:
            cols[k] = str(patch[k] or "").strip()
    if "shapes" in patch:
        cols["shapes"] = core.jdump(compose.clean_shapes(patch["shapes"]))
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
        if any(k in cols for k in ("t_wrong", "t_fixed", "details", "correct_note", "shapes")):
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
    """Extract both frames, draw the marks on the miss, build the composite and the zoom, and store them."""
    con = core.db()
    try:
        mr = con.execute("SELECT * FROM moments WHERE id=?", (mid,)).fetchone()
        miss = con.execute("SELECT * FROM misses WHERE id=?", (mr["miss_id"],)).fetchone()
    finally:
        con.close()
    wp = media.resolve_media(miss["wrong_path"])
    wpng = media.frame_file(wp, mr["t_wrong"])
    wrong = Image.open(wpng).convert("RGB")
    fpng, fixed = None, None
    if miss["fixed_path"]:                   # no right version: only the miss is cut and marked
        fpng = media.frame_file(media.resolve_media(miss["fixed_path"]), mr["t_fixed"])
        fixed = Image.open(fpng).convert("RGB")
    shapes = compose.clean_shapes(json.loads(mr["shapes"] or "[]"))
    meta = {"title": miss["title"], "model": miss["model"], "run": miss["run"], "video": miss["video"], "t_wrong": mr["t_wrong"],
            "t_fixed": mr["t_fixed"], "sample": f"S{mid}"}
    comp, zoom, ann, ann_f = compose.build(wrong, fixed, shapes, meta, mr["details"] or miss["explanation"] or "", mr["correct_note"] or "")
    d = sample_dir(mid)
    d.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(wpng, d / "wrong.png")
    ann.save(d / "annotated.png")
    for nm in ("fixed.png", "annotated_fixed.png"):
        if (d / nm).exists():
            (d / nm).unlink()
    if fixed is not None:
        shutil.copyfile(fpng, d / "fixed.png")
        ann_f.save(d / "annotated_fixed.png")
    comp.save(d / "composite.png", optimize=True)
    if zoom:
        zoom.save(d / "zoom.png", optimize=True)
    elif (d / "zoom.png").exists():
        (d / "zoom.png").unlink()
    con = core.db()
    try:
        con.execute("UPDATE moments SET w=?, h=?, updated=? WHERE id=?", (wrong.width, wrong.height, core.now(), mid))
        con.commit()
    finally:
        con.close()
    (d / "meta.json").write_text(json.dumps({"moment": mid, "miss": miss["id"], **meta, "shapes": shapes}, ensure_ascii=False, indent=1), encoding="utf-8")


def moment_file(mid: int, kind: str) -> Path | None:
    name = {"composite": "composite.png", "zoom": "zoom.png", "wrong": "wrong.png", "fixed": "fixed.png", "annotated": "annotated.png", "annotated_fixed": "annotated_fixed.png"}.get(kind)
    p = sample_dir(mid) / (name or "x")
    return p if name and p.is_file() else None


def moment_view(r, miss: dict) -> dict:
    mid = r["id"]
    kinds = [k for k in (r["kinds"] or "").split(",") if k] or miss.get("kinds", [])
    d = sample_dir(mid)
    return {"moment_id": mid, "id": f"S{mid}", "miss_id": miss["id"], "title": miss["title"], "explanation": miss["explanation"], "details": r["details"],
            "correct_note": r["correct_note"], "kinds": kinds, "model": miss["model"], "mode": miss["mode"], "product": miss["product"],
            "run": miss["run"], "video": miss["video"], "t_wrong": r["t_wrong"], "t_fixed": r["t_fixed"], "shapes": json.loads(r["shapes"] or "[]"),
            "status": r["status"], "miss_status": miss["status"], "created": r["created"], "updated": r["updated"],
            "has_right": bool(miss.get("fixed_path")), "image_url": f"/api/moments/{mid}/image?kind=composite", "zoom_url": f"/api/moments/{mid}/image?kind=zoom" if (d / "zoom.png").exists() else "",
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
