"""Loupe flags: the Higgsfield flag record (replaces ATP/caught_flags.html) with computed evidence."""
from __future__ import annotations

import base64
import json
import re
import threading
import uuid
from pathlib import Path

from . import core, facts, media, runs

STATUSES = ("open", "cleared", "persistent", "archived")
RAN = ("completed", "accepted", "superseded")
_lock = threading.Lock()
_corpus_cache: dict = {}


# ------------------------------------------------------------------ files (thumbnails)
def flag_dir(fid: str) -> Path:
    return core.DATA / "flags" / core.safe_name(fid, 40)


def _datauri_bytes(s: str):
    m = re.match(r"^data:([\w/+.-]+);base64,(.*)$", s or "", re.S)
    return (m.group(1), base64.b64decode(m.group(2))) if m else (None, None)


def _strip_blobs(fid: str, doc: dict) -> dict:
    """Data-URI thumbnails inside the document go to files; the document keeps their file names."""
    d = flag_dir(fid)
    for i, it in enumerate(doc.get("inputs") or []):
        it.pop("thumb_url", None)
        th = it.pop("thumb", None)
        if th:
            _, b = _datauri_bytes(th)
            if b:
                d.mkdir(parents=True, exist_ok=True)
                (d / f"in{i}.jpg").write_bytes(b)
                it["thumb_file"] = f"in{i}.jpg"
    part = (doc.get("trigger") or {}).get("part")
    if isinstance(part, dict):
        part.pop("frame_url", None)
    if isinstance(part, dict) and part.get("frame"):
        _, b = _datauri_bytes(part.pop("frame"))
        if b:
            d.mkdir(parents=True, exist_ok=True)
            (d / "part.jpg").write_bytes(b)
            part["frame_file"] = "part.jpg"
    return doc


def _with_urls(doc: dict) -> dict:
    fid = doc.get("id", "")
    for it in doc.get("inputs") or []:
        if it.get("thumb_file"):
            it["thumb_url"] = f"/api/flags/{fid}/file/{it['thumb_file']}"
    part = (doc.get("trigger") or {}).get("part")
    if isinstance(part, dict) and part.get("frame_file"):
        part["frame_url"] = f"/api/flags/{fid}/file/{part['frame_file']}"
    return doc


def flag_file(fid: str, name: str) -> Path | None:
    p = flag_dir(fid) / core.safe_name(name, 60)
    return p if p.is_file() else None


# ------------------------------------------------------------------ rows
def _blob(doc: dict) -> str:
    w, t = doc.get("where") or {}, doc.get("trigger") or {}
    parts = [doc.get("id"), doc.get("title"), doc.get("kind"), doc.get("status"), doc.get("response"), doc.get("prompt"), doc.get("outcome"),
             doc.get("changed"), doc.get("rule"), t.get("text"), t.get("cause"), t.get("prompt_span"), " ".join(t.get("tags") or []),
             " ".join(t.get("assets") or []), " ".join(t.get("assets_unmatched") or []), " ".join(str(v) for v in w.values()),
             " ".join(f"{i.get('label', '')} {i.get('name', '')} {i.get('ref', '')}" for i in doc.get("inputs") or []),
             " ".join(str(n.get("note", "")) for n in doc.get("log") or [])]
    return " ".join(str(p) for p in parts if p).lower()


def _upsert(con, doc: dict):
    w, t = doc.get("where") or {}, doc.get("trigger") or {}
    seq = int(re.sub(r"\D", "", doc["id"]) or 0)
    con.execute("""INSERT INTO flags(id,seq,created,updated,status,kind,run,video,attempt,model,mode,title,tags,search,doc)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(id) DO UPDATE SET updated=excluded.updated,status=excluded.status,kind=excluded.kind,run=excluded.run,
                   video=excluded.video,attempt=excluded.attempt,model=excluded.model,mode=excluded.mode,title=excluded.title,
                   tags=excluded.tags,search=excluded.search,doc=excluded.doc""",
                (doc["id"], seq, doc.get("created") or core.now(), doc.get("updated") or core.now(), doc.get("status") or "open",
                 doc.get("kind") or "other", str(w.get("run", "")), core.nn(w.get("video", "")), str(w.get("attempt", "")),
                 str(w.get("model", "")), str(w.get("mode", "")), doc.get("title") or "", " ".join(t.get("tags") or []), _blob(doc), core.jdump(doc)))
    con.execute("DELETE FROM flag_inputs WHERE flag_id=?", (doc["id"],))
    for i, it in enumerate(doc.get("inputs") or []):
        con.execute("INSERT INTO flag_inputs(flag_id,idx,sha1,name,role,suspect) VALUES(?,?,?,?,?,?)",
                    (doc["id"], i, (it.get("facts") or {}).get("sha1") or it.get("sha1") or "", it.get("file") or it.get("name") or "", it.get("role") or "",
                     1 if it.get("suspect") else 0))


def next_id(con) -> str:
    r = con.execute("SELECT MAX(seq) AS m FROM flags").fetchone()
    return f"CF-{(r['m'] or 0) + 1:03d}"


def save(doc: dict, actor: str = "system", create: bool = False) -> dict:
    """Create or replace a flag document.  The server assigns the id unless a numeric CF-nnn is given and free."""
    with _lock:
        con = core.db()
        try:
            fid = str(doc.get("id") or "")
            exists = con.execute("SELECT 1 FROM flags WHERE id=?", (fid,)).fetchone() if fid else None
            if create and (not re.match(r"^CF-\d+$", fid) or exists):
                fid = next_id(con)
            elif not fid:
                fid = next_id(con)
            doc["id"] = fid
            doc.setdefault("created", core.now())
            doc["updated"] = core.now()
            doc.setdefault("status", "open")
            if doc["status"] not in STATUSES:
                doc["status"] = "open"
            _strip_blobs(fid, doc)
            try:
                doc["evidence"] = compute_evidence(con, doc, keep_client=True)
            except Exception as e:  # noqa: BLE001  evidence must never block recording a flag
                doc.setdefault("evidence", {})["error"] = str(e)[:300]
            _upsert(con, doc)
            con.commit()
        finally:
            con.close()
    core.audit(actor, "flag.save", doc["id"], doc.get("title", ""))
    return get(doc["id"])


def get(fid: str) -> dict | None:
    con = core.db()
    try:
        r = con.execute("SELECT doc FROM flags WHERE id=?", (fid,)).fetchone()
        return _with_urls(json.loads(r["doc"])) if r else None
    finally:
        con.close()


def delete(fid: str, actor: str):
    con = core.db()
    try:
        con.execute("DELETE FROM flags WHERE id=?", (fid,))
        con.execute("DELETE FROM flag_inputs WHERE flag_id=?", (fid,))
        con.commit()
    finally:
        con.close()
    core.audit(actor, "flag.delete", fid)


_OPS = ("status", "kind", "tag", "model", "run", "video", "asset", "mode", "attempt")


def search(q: str = "", status: str = "", limit: int = 500, offset: int = 0) -> tuple[int, list[dict]]:
    where, args, words_ = [], [], []
    for tok in re.findall(r'(?:[a-z]+:"[^"]*"|\S+)', (q or "").lower()):
        m = re.match(r'^([a-z]+):"?(.*?)"?$', tok)
        if m and m.group(1) in _OPS:
            k, v = m.group(1), m.group(2)
            if k in ("status", "kind", "model", "run", "mode", "attempt"):
                where.append(f"lower({k}) LIKE ?"); args.append(f"%{v}%")
            elif k == "video":
                where.append("video=?"); args.append(core.nn(v))
            elif k == "tag":
                where.append("(' '||tags||' ') LIKE ?"); args.append(f"% {v} %")
            elif k == "asset":
                where.append("search LIKE ?"); args.append(f"%{v}%")
        else:
            words_.append(tok)
    for w in words_:
        where.append("search LIKE ?"); args.append(f"%{w}%")
    if status:
        where.append("status=?"); args.append(status)
    sql = " WHERE " + " AND ".join(where) if where else ""
    con = core.db()
    try:
        total = con.execute("SELECT COUNT(*) AS n FROM flags" + sql, args).fetchone()["n"]
        rows = con.execute("SELECT doc FROM flags" + sql + " ORDER BY seq DESC LIMIT ? OFFSET ?", args + [limit, offset]).fetchall()
        return total, [_with_urls(json.loads(r["doc"])) for r in rows]
    finally:
        con.close()


def stats() -> dict:
    con = core.db()
    try:
        def grp(col):
            return {r[0] or "": r[1] for r in con.execute(f"SELECT {col}, COUNT(*) FROM flags GROUP BY {col} ORDER BY 2 DESC")}
        tags = {}
        for r in con.execute("SELECT tags FROM flags"):
            for t in (r["tags"] or "").split():
                tags[t] = tags.get(t, 0) + 1
        assets = [dict(name=r["name"], sha1=r["sha1"], flags=r["n"]) for r in con.execute(
            "SELECT name, sha1, COUNT(DISTINCT flag_id) n FROM flag_inputs WHERE suspect=1 GROUP BY COALESCE(NULLIF(sha1,''),name) ORDER BY n DESC LIMIT 15")]
        return {"total": con.execute("SELECT COUNT(*) FROM flags").fetchone()[0], "by_status": grp("status"), "by_kind": grp("kind"),
                "by_model": grp("model"), "by_mode": grp("mode"), "tags": dict(sorted(tags.items(), key=lambda kv: -kv[1])), "suspect_assets": assets}
    finally:
        con.close()


# ------------------------------------------------------------------ evidence (facts only)
def _run_state(run: str):
    d = runs.run_dir(run)
    return d, (runs._json(d / "state.json") if d else None) or {}


def _timing(doc: dict) -> dict:
    w = doc.get("where") or {}
    d, st = _run_state(w.get("run", ""))
    job, fp = str(doc.get("job") or ""), str(doc.get("fingerprint") or "")
    for s in st.get("submissions") or []:
        if (s.get("job_id") and s["job_id"] in job) or (fp and s.get("fingerprint") and s["fingerprint"] in fp):
            hist = s.get("history") or []
            def at(to):
                return next((h["at"] for h in hist if h.get("to") == to), None)
            sub, fail = at("submitted"), at("failed")
            out = {"submitted_at": sub, "failed_at": fail, "ledger_status": s.get("status"), "flagged_mark": bool(s.get("flagged"))}
            try:
                from datetime import datetime
                if sub and fail:
                    sec = (datetime.fromisoformat(fail) - datetime.fromisoformat(sub)).total_seconds()
                    out["seconds_to_fail"] = round(sec)
            except ValueError:
                pass
            return out
    return {}


def _card_excerpt(doc: dict) -> str:
    w = doc.get("where") or {}
    d = runs.run_dir(w.get("run", ""))
    if not d:
        return ""
    refs = {i.get("ref") for i in doc.get("inputs") or [] if str(i.get("ref", "")).startswith("V")}
    m = re.search(r"\bV(\d{1,3})\b", str(w.get("reference", "")))
    if m:
        refs.add(f"V{int(m.group(1)):02d}")
    out = []
    for r in sorted(x for x in refs if x):
        for cand in (d / "20-analysis" / "cards" / f"{r}.md", d / "20-analysis" / "cards" / f"V{int(re.sub(r'\D', '', r) or 0):02d}.md"):
            if cand.is_file():
                lines = cand.read_text(encoding="utf-8", errors="replace").splitlines()
                take, n = [], 0
                for i, ln in enumerate(lines):
                    if re.search(r"(?i)source preparation|guardrail|people|person|face|celebrit|real |text on screen|overlay text|watermark|handle", ln):
                        take.append(ln.strip()[:240]); n += 1
                    if n >= 14:
                        break
                if take:
                    out.append(f"{cand.stem}:\n" + "\n".join(take))
                break
    return "\n\n".join(out)[:2500]


def _prompts_of_requests() -> list[tuple[bool, set]]:
    """(flagged?, term set) of every known request prompt: ran/failed requests from the runs, plus the prompts of all flags."""
    dirs = sorted(x for r in runs.runs_roots() if r.is_dir() for x in r.iterdir())
    sig = []
    for d in dirs:
        sp = d / "state.json"
        if sp.is_file():
            sig.append((str(d), sp.stat().st_mtime_ns))
    key = tuple(sig)
    if _corpus_cache.get("key") == key:
        return _corpus_cache["val"]
    out = []
    for d in dirs:
        st = runs._json(d / "state.json")
        if not isinstance(st, dict):
            continue
        for s in st.get("submissions") or []:
            rf = Path(str(s.get("request_file", "")))
            req = runs._json(rf) if rf.is_file() else None
            if not isinstance(req, dict):
                continue
            prm = req.get("params") if isinstance(req.get("params"), dict) else req
            pr = str(prm.get("prompt") or "")
            if pr:
                out.append((bool(s.get("flagged")), s.get("status") in RAN and not s.get("flagged"), facts.term_set(pr)))
    _corpus_cache["key"], _corpus_cache["val"] = key, out
    return out


def compute_evidence(con, doc: dict, keep_client: bool = True) -> dict:
    ev = dict(doc.get("evidence") or {}) if keep_client else {}
    ev["computed_at"] = core.now()
    ev["timing"] = _timing(doc)
    if ev["timing"].get("seconds_to_fail") is not None:
        s = ev["timing"]["seconds_to_fail"]
        ev["timing"]["reading"] = ("failed within 30 s of submission: typical of a check on the prompt or the inputs before rendering" if s <= 30
                                   else "failed after rendering had started: typical of a check on the output")
    card = _card_excerpt(doc)
    if card:
        ev["reference_card"] = card
    pf = facts.prompt_facts(doc.get("prompt", ""))
    ev["prompt"] = pf
    # history ----------------------------------------------------------------------------------
    try:
        runs.index_usage(budget=6)
    except Exception as e:  # noqa: BLE001
        ev["usage_index_error"] = str(e)[:200]
    hist = {"inputs": [], "similar_flags": [], "prompt_terms": {}}
    for i, it in enumerate(doc.get("inputs") or []):
        sha = (it.get("facts") or {}).get("sha1") or ""
        name = it.get("file") or it.get("name") or ""
        if sha:
            u = con.execute("SELECT status, flagged, run FROM usage WHERE sha1=?", (sha,)).fetchall()
            fl = [r["flag_id"] for r in con.execute("SELECT DISTINCT flag_id FROM flag_inputs WHERE sha1=? AND flag_id<>?", (sha, doc.get("id", "")))]
        elif name:
            u = con.execute("SELECT status, flagged, run FROM usage WHERE name=?", (name,)).fetchall()
            fl = [r["flag_id"] for r in con.execute("SELECT DISTINCT flag_id FROM flag_inputs WHERE name=? AND flag_id<>?", (name, doc.get("id", "")))]
        else:
            continue
        hist["inputs"].append({"label": it.get("label"), "name": name, "matched_by": "sha1" if sha else "name",
                               "jobs_ran": sum(1 for r in u if r["status"] in RAN and not r["flagged"]),
                               "jobs_flagged": sum(1 for r in u if r["flagged"]),
                               "jobs_failed_other": sum(1 for r in u if r["status"] == "failed" and not r["flagged"]),
                               "other_flags": fl, "runs": sorted({r["run"] for r in u})[:6]})
    mine = facts.term_set(doc.get("prompt", ""))
    if mine:
        for r in con.execute("SELECT id, status, doc FROM flags WHERE id<>?", (doc.get("id", ""),)):
            other = json.loads(r["doc"])
            ot = facts.term_set(other.get("prompt", ""))
            if ot:
                j = len(mine & ot) / len(mine | ot)
                if j >= 0.35:
                    shared = [x.get("name") for x in doc.get("inputs") or [] if x.get("name") and x.get("name") in {y.get("name") for y in other.get("inputs") or []}]
                    hist["similar_flags"].append({"id": r["id"], "status": r["status"], "similarity": round(j, 2), "shared_inputs": shared[:6]})
        hist["similar_flags"] = sorted(hist["similar_flags"], key=lambda x: -x["similarity"])[:4]
        corpus = _prompts_of_requests()
        flagged_sets = [t for f, p, t in corpus if f]
        for r in con.execute("SELECT doc FROM flags WHERE id<>?", (doc.get("id", ""),)):
            flagged_sets.append(facts.term_set(json.loads(r["doc"]).get("prompt", "")))
        flagged_sets.append(mine)
        ran_sets = [t for f, p, t in corpus if p]
        hist["prompt_terms"] = {"flagged_prompts": len(flagged_sets), "ran_prompts": len(ran_sets), "terms": []}
        if len(flagged_sets) >= 2 and ran_sets:
            cand = []
            for term in mine:
                fc = sum(1 for s in flagged_sets if term in s)
                if fc >= 2 and not any(term in s for s in ran_sets):
                    cand.append((fc, len(term.split()), term))
            hist["prompt_terms"]["terms"] = [{"term": t, "flagged_prompts": c} for c, _, t in sorted(cand, reverse=True)[:15]]
    ev["history"] = hist
    # diff against the accepted request --------------------------------------------------------------
    cl = doc.get("cleared") or {}
    if cl.get("prompt") or cl.get("inputs"):
        new = {"prompt": cl.get("prompt", ""), "inputs": cl.get("inputs") or doc.get("inputs") or [], "params": cl.get("params") or {}}
        old = {"prompt": doc.get("prompt", ""), "inputs": doc.get("inputs") or [], "params": doc.get("params") or {}}
        ev["diff_to_cleared"] = facts.diff_facts(old, new)
    ev["signals"] = signals(doc, ev)
    return ev


def signals(doc: dict, ev: dict) -> list[dict]:
    """Plain statements read off the facts, each labelled [E] (evidence).  They suggest where to look; they are not a verdict."""
    out = []

    def add(text):
        out.append({"basis": "E", "text": text})
    t = ev.get("timing") or {}
    if t.get("seconds_to_fail") is not None:
        add(f"Failed {t['seconds_to_fail']} s after submission ({t.get('reading', '')}).")
    for h in (ev.get("history") or {}).get("inputs", []):
        nm = f"{h.get('label', '')} {h.get('name', '')}".strip()
        if h["jobs_flagged"] and not h["jobs_ran"]:
            add(f"{nm}: in {h['jobs_flagged']} flagged job(s) and in no job that ran ({h['matched_by']} match)." + (f" Also in flags {', '.join(h['other_flags'])}." if h["other_flags"] else ""))
        elif h["jobs_ran"] and not h["jobs_flagged"]:
            add(f"{nm}: ran without a flag in {h['jobs_ran']} other job(s); it is not a trigger on its own.")
        elif h["jobs_ran"] and h["jobs_flagged"]:
            add(f"{nm}: in {h['jobs_flagged']} flagged and {h['jobs_ran']} ran job(s); the same input passes elsewhere.")
    for it in doc.get("inputs") or []:
        txt = (it.get("facts") or {}).get("text_seen")
        if txt:
            add(f"Text on screen in {it.get('label', '')} {it.get('name', '')}: " + "; ".join(txt[:8]))
    ph = (ev.get("history") or {}).get("prompt_terms") or {}
    if ph.get("terms"):
        add("Words or pairs in this prompt that appear in other flagged prompts and in no prompt that ran: " + ", ".join(f"'{x['term']}' ({x['flagged_prompts']})" for x in ph["terms"][:10]))
    elif ph.get("flagged_prompts", 0) < 2:
        add("Too few flagged prompts to compare wording yet (needs 2 or more).")
    for s in (ev.get("history") or {}).get("similar_flags", [])[:2]:
        add(f"Prompt resembles {s['id']} ({s['status']}, similarity {s['similarity']})" + (f"; shared inputs: {', '.join(s['shared_inputs'])}" if s["shared_inputs"] else "") + ".")
    d = ev.get("diff_to_cleared")
    if d:
        bits = []
        if d["inputs_removed"]:
            bits.append("inputs removed: " + ", ".join(d["inputs_removed"]))
        if d["inputs_added"]:
            bits.append("inputs added: " + ", ".join(d["inputs_added"]))
        if d["words_removed"]:
            bits.append("words removed: " + ", ".join(d["words_removed"][:14]))
        if d["params_changed"]:
            bits.append("parameters changed: " + ", ".join(d["params_changed"]))
        if bits:
            add("What changed in the request that was accepted: " + "; ".join(bits) + ".")
    if ev.get("reference_card"):
        add("The reference's analysis card records source flags (see the card excerpt).")
    return out


def refresh_facts(fid: str, actor: str) -> dict | None:
    """Recompute the per-input file facts (hash, size, on-screen text) and the evidence of one flag."""
    doc = get(fid)
    if not doc:
        return None
    for it in doc.get("inputs") or []:
        p = it.get("path")
        if p and Path(p).is_file():
            it["facts"] = facts.file_facts(p, ocr=True)
    return save(doc, actor)


# ------------------------------------------------------------------ offline outbox and the legacy page
def capture_add(b: dict) -> int:
    """Keep a raw tool response the hook saved (the id is what `caught-flag add --capture` attaches)."""
    con = core.db()
    try:
        cur = con.execute("INSERT INTO captures(ts,tool,run,agent,job,summary) VALUES(?,?,?,?,?,?)",
                          (b.get("ts") or core.now(), str(b.get("tool", ""))[:80], str(b.get("run", ""))[:80], str(b.get("agent", ""))[:80],
                           str(b.get("job", ""))[:80], str(b.get("summary", ""))[:400]))
        cid = cur.lastrowid
        f = core.DATA / "captures" / f"{cid}.json"
        f.write_text(json.dumps(b, ensure_ascii=False)[:400000], encoding="utf-8")
        con.execute("UPDATE captures SET file=? WHERE id=?", (str(f), cid))
        con.commit()
    finally:
        con.close()
    return cid


def ingest_outbox() -> int:
    """Flags the pipeline queued while Loupe was down (DATA/outbox/*.json) are recorded now."""
    n = 0
    ob = core.DATA / "outbox"
    for p in sorted(ob.glob("*.json")) if ob.is_dir() else []:
        try:
            job = json.loads(p.read_bytes().decode("utf-8"))
            doc = job["doc"]
            if job.get("op") == "capture":
                capture_add(doc)
            else:
                prov = doc.get("id", "")
                doc["id"] = "" if prov.startswith("CF-Q") else prov
                save(doc, "outbox", create=job.get("op") == "add")
            p.rename(p.with_suffix(".done"))
            n += 1
        except Exception as e:  # noqa: BLE001
            p.rename(p.with_suffix(".failed"))
            core.audit("system", "outbox.failed", p.name, str(e)[:300])
    return n


def import_legacy(html_path: Path) -> int:
    """Move the entries of the old caught_flags.html page into Loupe, keeping their ids."""
    raw = html_path.read_bytes().decode("utf-8")
    m = re.search(r'<script id="flags-data" type="application/json">(.*?)</script>', raw, re.S)
    if not m:
        raise ValueError("no flags-data block in " + str(html_path))
    ents = json.loads(m.group(1)).get("entries", [])
    n = 0
    for e in ents:
        e.setdefault("imported_from", "caught_flags.html")
        save(e, "import")
        n += 1
    return n
