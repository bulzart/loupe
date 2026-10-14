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
    for p in ctx.get("product_forms") or [x for x in (ctx.get("product"), ctx.get("product_details")) if x]:
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
    if not s.get("has_right", True):
        t.append("  NO RIGHT VERSION: a miss example only")
    elif s["correct_note"]:
        t.append(f"  CORRECT: {s['correct_note'][:400]}")
    if with_path:
        t.append(f"  image (left: the miss with the marks, right: the correct frame, below: zoom): {s['composite_path']}" if s.get("has_right", True)
                 else f"  image (the miss with its marks and a zoom; this sample has NO right version, it is a miss example only): {s['composite_path']}")
    return "\n".join(t)


def brief(ctx: dict, limit: int = 8) -> dict:
    sam = search(ctx, limit)
    text = (f"{len(sam)} sample(s) from the library that match this video ({ctx.get('model') or 'any model'}; "
            f"{', '.join(ctx.get('kinds') or []) or 'any kind'}). Look at each image; compare the marked region with the same place in the video under QC.\n\n"
            + "\n\n".join(sample_lines(s) for s in sam)) if sam else "No sample in the library matches this video."
    return {"mode": "brief", "samples": [{k: s[k] for k in ("id", "title", "kinds", "model", "details", "correct_note", "has_right", "composite_path", "image_url", "score")} for s in sam],
            "text": text, "library_size": len(library())}


# ------------------------------------------------------------------ keeper
def _digest() -> str:
    lib = library()
    lines = []
    for s in lib:
        compact = len(lib) > 250
        lines.append(f"{s['id']} | {','.join(s['kinds'])} | {s['model'] or 'any'} | {s['title']}"
                     + ("" if compact else f" | miss: {s['explanation'][:240]} | wrong here: {s['details'][:240]} | correct: {s['correct_note'][:240]}")
                     + ("" if s.get("has_right", True) else " | NO RIGHT VERSION (a miss example only)")
                     + f" | image: {s['composite_path']}")
    return "\n".join(lines) or "(the library is empty)"


def _sig() -> str:
    lib = library()
    return f"{len(lib)}:{max([s['updated'] for s in lib] or [''])}"


ROLE = ("You are the Loupe keeper of a video-generation pipeline. You hold a library of samples: each is a generation miss (a frame of a wrong "
        "video with the admin's marks) next to the same moment of the right version, with the admin's own words. Rules for every question: you only "
        "READ; you never write, edit or create anything, and you never add to the library (only the admin does, in the web app). Cite only sample ids "
        "that exist in your library; never invent a sample or a rule; judge only from the samples and the admin's words, never from general knowledge "
        "of video models. Some samples are marked NO RIGHT VERSION: they are miss examples only (nothing was corrected); never invent what a right "
        "version would look like. For QC they still show the wrong pattern to look for; for planning, give a hint from one only when the admin's words "
        "let you state the right outcome positively, or the lever is a plain restatement at the matching beat, and then at most medium confidence. "
        "You may Read a sample's image (path given) to describe it, nothing else. "
        "Reason from mechanisms, not labels: a sample is relevant when it and the video share a product detail (form, size, opening, posture) and a "
        "scene mechanism, and you say which; a sample about another product form applies only through the same mechanism. Every why, check, trigger, "
        "right_outcome and lever is a CONDITION tied to this video's product details and beat, never a universal: no never, always, must, avoid or "
        "should-not, no product-wide ban or prescription. A sample is one admin judgement about one moment; two samples that seem to disagree are two "
        "conditions, so name what separates them. "
        "QC QUESTION (the video exists; an agent is checking it): answer ONE JSON object and no other text: "
        '{"relevant":[{"id":"S12","why":"<why it applies>","check":"<what exactly to compare in the video under QC>","wrong_pattern":"<the miss in a phrase>"}],'
        '"advice":"<at most 80 words>","not_covered":"<what in the question the library does not cover, or empty>"}. '
        "PLANNING QUESTION (the video has NOT been generated; the prompt writer shows the beats of its plan and asks which past misses could matter): "
        "the writer will act on what you say, and over-correcting from old misses distorts a good plan, so be sparse and exact. Your default answer is NO "
        "hints. Give a hint only when a beat of THIS plan matches the sample's trigger AND the model, mode and product form are compatible; one sure "
        "hint beats three guesses; at most 3, best first. Never transfer a sample to another kind of object or action unless the same mechanism is at "
        "work (say why in caveat); set model_independent true only when the miss comes from physics or logic that any video model would show. A hint "
        "is a risk plus a POSITIVE lever: right_outcome says what the right version shows, in positive words; lever names which of the writer's own tools "
        "to use at that beat (restating a count or state after an event, the one targeted exclusion for the product's own use, the reference view, the "
        "beat written as start state, operation, result, the anchor) and how. Never tell the writer to put the wrong behaviour into the prompt, to add a "
        "list of negatives, to add elements or camera moves, to redesign the plan, or to touch the source's story or timing. caveat says when it does NOT "
        "apply. Answer ONE JSON object and no other text: "
        '{"hints":[{"id":"S12","trigger":"<the plan situation in which this miss arises>","right_outcome":"<what the right version shows, positive>",'
        '"lever":"<which tool, at which beat, how>","caveat":"<when it does not apply>","confidence":"high|medium|low","model_independent":false}],'
        '"none_reason":"<one phrase when hints is empty>"}')


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


UNIVERSAL = re.compile(r"\b(never|always|must not|must|should not|shouldn'?t|do not|don'?t|avoid)\b", re.I)     # a hint is a condition for this plan, not a command


def plan_hints_from(parsed, ctx: dict, lib: dict, cap: int = 3) -> tuple[list[dict], int]:
    """The keeper's planning answer, held to the rules in code too: only real ids, no universal wording, only high or medium confidence, only a compatible model
    (unless the keeper says the miss is model-independent), a trigger and a lever present, three at most, short fields.  (hints, held_back)"""
    out, held = [], 0
    raw = (parsed or {}).get("hints") if isinstance(parsed, dict) else None
    for r in (raw or [])[:8]:
        if not isinstance(r, dict) or r.get("id") not in lib:
            held += 1
            continue
        s = lib[r["id"]]
        conf = str(r.get("confidence", "")).strip().lower()
        sm, cm = (s.get("model") or "").lower(), (ctx.get("model") or "").lower()
        indep = bool(r.get("model_independent"))
        if UNIVERSAL.search(" ".join(str(r.get(k, "")) for k in ("trigger", "right_outcome", "lever"))):
            held += 1
            continue
        if conf not in ("high", "medium") or not str(r.get("trigger", "")).strip() or not str(r.get("lever", "")).strip() \
                or (sm and cm and _family(sm) != _family(cm) and not indep):
            held += 1
            continue
        out.append({"id": s["id"], "trigger": str(r["trigger"]).strip()[:220], "right_outcome": str(r.get("right_outcome", "")).strip()[:220],
                    "lever": str(r["lever"]).strip()[:260], "caveat": str(r.get("caveat", "")).strip()[:180], "confidence": conf, "model_independent": indep})
    out.sort(key=lambda h: 0 if h["confidence"] == "high" else 1)
    held += max(0, len(out) - cap)
    return out[:cap], held


def consult(key_name: str, ctx: dict, question: str, purpose: str = "qc") -> dict:
    """purpose 'qc': which samples apply to a video that exists (answer carries image paths).  purpose 'plan': which past misses could matter for the
    plan of a video that has not been generated (answer: a few text-only hints, usually none).  The same keeper holds the library for both."""
    t0 = time.time()
    plan = purpose == "plan"
    lib_now = library()
    pre = search({**ctx, "q": question}, 12)
    fallback = brief({**ctx, "q": question})
    res = None
    err = ""
    cost = 0.0
    if not lib_now:                      # nothing to hold: no keeper session is started for an empty library
        res = {"mode": "empty", "purpose": purpose, "hints": [], "relevant": [], "samples": [], "library_size": 0, "text": "The library is empty."}
    with _keeper_lock:
        try:
            if res is not None:
                raise StopIteration
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
            who = (f"run {ctx.get('run') or '?'} video {ctx.get('video') or '?'} (model {ctx.get('model') or '?'}, mode {ctx.get('mode') or '?'}, "
                   f"product {ctx.get('product') or '?'}; product details: {str(ctx.get('product_details') or '-')[:400]})")
            if plan:
                prompt = (f"PLANNING question for {who}. The video has not been generated. The writer's plan, one line per beat:\n{question}\n"
                          f"{delta}\nAnswer with the PLANNING JSON object only (an empty hints list is the normal answer).")
            else:
                prompt = (f"QC question for {who}:\n{question}\n\nContext: {json.dumps({k: ctx.get(k) for k in ('kinds', 'checkpoints', 'product_forms') if ctx.get(k)}, ensure_ascii=False)}\n"
                          f"Server pre-ranking (ids, best first): {', '.join(s['id'] for s in pre) or 'none'}.{delta}\nAnswer with the JSON object only.")
            d = _run_claude(prompt, st["session_id"], True, st["model"])
            cost += float(d.get("total_cost_usd") or 0)
            st["turns"] = st.get("turns", 0) + 1
            st["library_sig"] = sig
            st["last_used"] = core.now()
            core.setting_set("keeper", st)
            parsed = _json_of(d.get("result", ""))
            ids = {s["id"]: s for s in library()}
            if plan:
                hints, held = plan_hints_from(parsed, ctx, ids)
                res = {"mode": "keeper", "purpose": "plan", "hints": hints, "held_back": held, "library_size": len(ids),
                       "none_reason": str((parsed or {}).get("none_reason", ""))[:160] if isinstance(parsed, dict) and not hints else ""}
            else:
                rel = []
                for r in (parsed or {}).get("relevant", []) if isinstance(parsed, dict) else []:
                    if isinstance(r, dict) and r.get("id") in ids:      # only samples that exist
                        s = ids[r["id"]]
                        rel.append({"id": s["id"], "why": str(r.get("why", ""))[:400], "check": str(r.get("check", ""))[:400],
                                    "wrong_pattern": str(r.get("wrong_pattern", ""))[:200], "composite_path": s["composite_path"], "image_url": s["image_url"],
                                    "details": s["details"], "correct_note": s["correct_note"], "has_right": s.get("has_right", True)})
                res = {"mode": "keeper", "purpose": "qc", "relevant": rel, "advice": str((parsed or {}).get("advice", ""))[:600] if isinstance(parsed, dict) else str(d.get("result", ""))[:600],
                       "not_covered": str((parsed or {}).get("not_covered", ""))[:400] if isinstance(parsed, dict) else "", "library_size": len(ids)}
        except StopIteration:
            pass
        except Exception as e:  # noqa: BLE001
            err = str(e)[:300]
    if res is None:
        # no keeper: QC still gets the plain best-match list; planning gets NOTHING (without the keeper's judgement a raw list would over-steer a plan)
        res = ({"mode": "unavailable", "purpose": "plan", "hints": [], "library_size": len(lib_now), "keeper_error": err} if plan
               else {**fallback, "mode": "fallback", "purpose": "qc", "keeper_error": err})
    ms = int((time.time() - t0) * 1000)
    ids_used = ",".join(x["id"] for x in (res.get("hints") if plan else res.get("relevant", res.get("samples", []))) or [])
    con = core.db()
    try:
        con.execute("INSERT INTO consults(ts,key_name,run,video,question,sample_ids,answer,ms,mode,cost,purpose) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                    (core.now(), key_name, ctx.get("run") or "", ctx.get("video") or "", question[:1000], ids_used, core.jdump(res)[:6000], ms, res["mode"], cost, purpose))
        con.commit()
    finally:
        con.close()
    res["ms"] = ms
    return res
