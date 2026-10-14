"""Loupe QC: the sample library as the QC phase sees it, and the keeper.

The keeper is ONE separate headless Claude session (its own memory, resumed by id), started by Loupe, whose only tool is Read.  It is
told the whole sample library, answers QC agents' questions about it, and can write nothing: the library is edited only by the admin
in the web app.  If the keeper cannot run, QC gets the deterministic brief instead.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import threading
import time
import uuid
from datetime import datetime, timedelta

from . import core, facts, misses

_keeper_lock = threading.Lock()
ROT_TURNS = 30           # a keeper session this long is replaced by a fresh one rebuilt from the library (nothing is lost: the library is the memory)
DEFAULT_MODEL = "claude-haiku-5-5"


# ------------------------------------------------------------------ the library
def library(status: str = "active") -> list[dict]:
    out = []
    for m in misses.listing(status="active" if status == "active" else ""):
        for mo in m["moments"]:
            if status == "active" and mo["status"] != "active":
                continue
            out.append(mo)
    return out


def _family(model: str) -> str:
    return re.sub(r"_(prime|turbo|lite|max|edit)$", "", model or "")


def score(s: dict, ctx: dict) -> float:
    sc = 0.0
    m, sm = (ctx.get("model") or "").lower(), (s.get("model") or "").lower()
    if m and sm:
        sc += 4 if m == sm else (2 if _family(m) == _family(sm) else 0)
    elif not sm:
        sc += 0.5
    if ctx.get("mode") and s.get("mode") and str(ctx["mode"]).lower().split("-")[0] in str(s["mode"]).lower():
        sc += 1
    ck = {str(k).lower() for k in (ctx.get("kinds") or [])}
    sc += 3 * len(ck & set(s.get("kinds") or []))
    blob = " ".join([s.get("title", ""), s.get("explanation", ""), s.get("details", ""), s.get("correct_note", ""), s.get("product", "")]).lower()
    for p in ctx.get("product_forms") or ([ctx["product"]] if ctx.get("product") else []):
        for tok in re.findall(r"[a-z0-9]{4,}", str(p).lower()):
            if tok in blob:
                sc += 1.5
                break
    toks = [t for t in facts.words(ctx.get("q", "")) if len(t) >= 3]
    sc += min(6.0, 0.6 * sum(1 for t in set(toks) if t in blob))
    try:
        if datetime.fromisoformat(s["created"]) > datetime.now().astimezone() - timedelta(days=30):
            sc += 0.3
    except (KeyError, ValueError):
        pass
    return sc


def search(ctx: dict, limit: int = 8) -> list[dict]:
    lib = library()
    scored = sorted(((score(s, ctx), s) for s in lib), key=lambda x: (-x[0], -x[1]["moment_id"]))
    res = [dict(s, score=round(sc, 2)) for sc, s in scored if sc > 0]
    if not res and not any(ctx.get(k) for k in ("model", "kinds", "q", "product", "product_forms", "mode")):
        res = [dict(s, score=0.0) for s in lib]
    return res[:limit]


def sample_lines(s: dict, with_path: bool = True) -> str:
    t = [f"[{s['id']}] kinds: {', '.join(s['kinds']) or '-'} | model {s['model'] or 'any'} | {s['run'] or 'no run'} video {s['video'] or '-'} @ {s['t_wrong']:.2f}s",
         f"  MISS: {s['title']}" + (f". {s['explanation'][:400]}" if s["explanation"] else "")]
    if s["details"]:
        t.append(f"  WRONG HERE: {s['details'][:400]}")
    if s["correct_note"]:
        t.append(f"  CORRECT: {s['correct_note'][:400]}")
    if with_path:
        t.append(f"  image (left: the miss with the marks, right: the correct frame, below: zoom): {s['composite_path']}")
    return "\n".join(t)


def brief(ctx: dict, limit: int = 8) -> dict:
    sam = search(ctx, limit)
    text = (f"{len(sam)} sample(s) from the library that match this video ({ctx.get('model') or 'any model'}; "
            f"{', '.join(ctx.get('kinds') or []) or 'any kind'}). Look at each image; compare the marked region with the same place in the video under QC.\n\n"
            + "\n\n".join(sample_lines(s) for s in sam)) if sam else "No sample in the library matches this video."
    return {"mode": "brief", "samples": [{k: s[k] for k in ("id", "title", "kinds", "model", "details", "correct_note", "composite_path", "image_url", "score")} for s in sam],
            "text": text, "library_size": len(library())}


# ------------------------------------------------------------------ keeper
def _digest() -> str:
    lib = library()
    lines = []
    for s in lib:
        compact = len(lib) > 250
        lines.append(f"{s['id']} | {','.join(s['kinds'])} | {s['model'] or 'any'} | {s['title']}"
                     + ("" if compact else f" | miss: {s['explanation'][:240]} | wrong here: {s['details'][:240]} | correct: {s['correct_note'][:240]}")
                     + f" | image: {s['composite_path']}")
    return "\n".join(lines) or "(the library is empty)"


def _sig() -> str:
    lib = library()
    return f"{len(lib)}:{max([s['updated'] for s in lib] or [''])}"


ROLE = ("You are the Loupe QC keeper of a video-generation pipeline. You hold a library of samples: each is a generation miss (a frame of a wrong "
        "video with the admin's marks) next to the same moment of the corrected video, with the admin's own words. QC agents ask you which samples "
        "apply to the video they are checking. Rules: you only READ; you never write, edit or create anything, and you never add to the library "
        "(only the admin does, in the web app). Cite only sample ids that exist in your library; never invent a sample or a rule. If the question "
        "has no match, say so. You may Read a sample's image (path given) to describe it, nothing else. Answer with ONE JSON object and no other "
        'text: {"relevant":[{"id":"S12","why":"<why it applies>","check":"<what exactly to compare in the video under QC>","wrong_pattern":"<the miss in a phrase>"}],'
        '"advice":"<at most 80 words>","not_covered":"<what in the question the library does not cover, or empty>"}')


def _claude() -> str | None:
    return shutil.which("claude") or shutil.which("claude.exe") or shutil.which("claude.cmd")


def _run_claude(prompt: str, sid: str, resume: bool, model: str, timeout: int = 150) -> dict:
    exe = _claude()
    if not exe:
        raise RuntimeError("the claude CLI was not found on PATH")
    cmd = [exe, "-p", prompt, "--model", model, "--tools", "Read", "--allowedTools", "Read", "--strict-mcp-config", "--output-format", "json",
           "--max-turns", "6", "--append-system-prompt", ROLE, "--add-dir", str(core.DATA / "samples")]
    cmd += ["--resume", sid] if resume else ["--session-id", sid]
    kw = {"capture_output": True, "timeout": timeout, "cwd": str(core.DATA / "keeper"), "creationflags": getattr(subprocess, "CREATE_NO_WINDOW", 0)}
    cp = subprocess.run(cmd, **kw)
    raw = cp.stdout.decode("utf-8", "replace").strip()
    try:
        d = json.loads(raw.splitlines()[-1]) if raw else {}
    except ValueError:
        d = {}
    if cp.returncode != 0 or d.get("is_error") or not d:
        raise RuntimeError("keeper session failed: " + (d.get("result") or cp.stderr.decode("utf-8", "replace") or raw)[:300])
    return d


def keeper_status() -> dict:
    st = core.setting_get("keeper", {}) or {}
    st["model"] = core.setting_get("keeper_model", DEFAULT_MODEL)
    st["library"] = _sig()
    st["claude_found"] = bool(_claude())
    return st


def keeper_reset(actor: str = "admin"):
    core.setting_set("keeper", {})
    core.audit(actor, "keeper.reset")


def _json_of(text: str):
    t = (text or "").strip()
    t = re.sub(r"^```(?:json)?|```$", "", t, flags=re.M).strip()
    try:
        return json.loads(t)
    except ValueError:
        m = re.search(r"\{.*\}", t, re.S)
        if m:
            try:
                return json.loads(m.group(0))
            except ValueError:
                return None
    return None


def consult(key_name: str, ctx: dict, question: str) -> dict:
    t0 = time.time()
    pre = search({**ctx, "q": question}, 12)
    fallback = brief({**ctx, "q": question})
    res = None
    err = ""
    cost = 0.0
    with _keeper_lock:
        try:
            core.ensure_dirs()
            model = core.setting_get("keeper_model", DEFAULT_MODEL)
            st = core.setting_get("keeper", {}) or {}
            sig = _sig()
            if not st.get("session_id") or st.get("turns", 0) >= ROT_TURNS or st.get("model") != model:
                sid = str(uuid.uuid4())
                boot = (f"LIBRARY ({len(library())} samples; version {sig}). One line per sample: id | kinds | model | title | the admin's words | image path.\n\n"
                        f"{_digest()}\n\nYou now hold this library. Reply with the single word: ready.")
                d = _run_claude(boot, sid, False, model)
                cost += float(d.get("total_cost_usd") or 0)
                st = {"session_id": sid, "created": core.now(), "turns": 1, "model": model, "library_sig": sig, "bootstrapped": core.now()}
            delta = ""
            if st.get("library_sig") != sig:
                delta = "\nThe library changed since your last digest. Full current list again:\n" + _digest() + "\n"
            prompt = (f"QC question for run {ctx.get('run') or '?'} video {ctx.get('video') or '?'} (model {ctx.get('model') or '?'}, mode {ctx.get('mode') or '?'}, "
                      f"product {ctx.get('product') or '?'}):\n{question}\n\nContext: {json.dumps({k: ctx.get(k) for k in ('kinds', 'checkpoints', 'product_forms') if ctx.get(k)}, ensure_ascii=False)}\n"
                      f"Server pre-ranking (ids, best first): {', '.join(s['id'] for s in pre) or 'none'}.{delta}\nAnswer with the JSON object only.")
            d = _run_claude(prompt, st["session_id"], True, st["model"])
            cost += float(d.get("total_cost_usd") or 0)
            st["turns"] = st.get("turns", 0) + 1
            st["library_sig"] = sig
            st["last_used"] = core.now()
            core.setting_set("keeper", st)
            parsed = _json_of(d.get("result", ""))
            ids = {s["id"]: s for s in library()}
            rel = []
            for r in (parsed or {}).get("relevant", []) if isinstance(parsed, dict) else []:
                if isinstance(r, dict) and r.get("id") in ids:      # only samples that exist
                    s = ids[r["id"]]
                    rel.append({"id": s["id"], "why": str(r.get("why", ""))[:400], "check": str(r.get("check", ""))[:400],
                                "wrong_pattern": str(r.get("wrong_pattern", ""))[:200], "composite_path": s["composite_path"], "image_url": s["image_url"],
                                "details": s["details"], "correct_note": s["correct_note"]})
            res = {"mode": "keeper", "relevant": rel, "advice": str((parsed or {}).get("advice", ""))[:600] if isinstance(parsed, dict) else str(d.get("result", ""))[:600],
                   "not_covered": str((parsed or {}).get("not_covered", ""))[:400] if isinstance(parsed, dict) else "", "library_size": len(ids)}
        except Exception as e:  # noqa: BLE001
            err = str(e)[:300]
    if res is None:
        res = {**fallback, "mode": "fallback", "keeper_error": err}
    ms = int((time.time() - t0) * 1000)
    con = core.db()
    try:
        con.execute("INSERT INTO consults(ts,key_name,run,video,question,sample_ids,answer,ms,mode,cost) VALUES(?,?,?,?,?,?,?,?,?,?)",
                    (core.now(), key_name, ctx.get("run") or "", ctx.get("video") or "", question[:1000],
                     ",".join(s["id"] for s in res.get("relevant", res.get("samples", []))), core.jdump(res)[:6000], ms, res["mode"], cost))
        con.commit()
    finally:
        con.close()
    res["ms"] = ms
    return res
