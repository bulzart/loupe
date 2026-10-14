"""Loupe MCP server (stdio, standard library only): the QC samples and flag search as native tools for Claude agents.

Register once:   claude mcp add --scope user loupe -e LOUPE_API_KEY=<a qc:read key> -- python <this file>
The key decides what the tools may do (qc:read for the QC tools, flags:read for flags_search).  No tool writes anything.
"""
from __future__ import annotations

import base64
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import loupe_client as lc  # noqa: E402

TOOLS = [
    {"name": "qc_search", "description": "Search the QC sample library (generation misses with a wrong-vs-correct image) for the video under QC. Returns ids, titles, kinds and scores; open one with qc_get_sample.",
     "inputSchema": {"type": "object", "properties": {"model": {"type": "string"}, "mode": {"type": "string"}, "kinds": {"type": "array", "items": {"type": "string"}},
                                                      "product": {"type": "string"}, "q": {"type": "string", "description": "words describing the suspected miss"}, "limit": {"type": "integer"}}}},
    {"name": "qc_get_sample", "description": "One QC sample: the admin's words and the composite image (left: the miss with marks; right: the correct frame at the same moment; below: zoom).",
     "inputSchema": {"type": "object", "properties": {"id": {"type": "string", "description": "e.g. S12"}}, "required": ["id"]}},
    {"name": "qc_brief", "description": "A deterministic brief: the samples that match this video (model, kinds, words) with the text of each and the image path.",
     "inputSchema": {"type": "object", "properties": {"run": {"type": "string"}, "video": {"type": "string"}, "model": {"type": "string"}, "mode": {"type": "string"},
                                                      "kinds": {"type": "array", "items": {"type": "string"}}, "product_forms": {"type": "array", "items": {"type": "string"}},
                                                      "q": {"type": "string"}, "limit": {"type": "integer"}}}},
    {"name": "qc_consult", "description": "Ask the QC keeper (one separate read-only Claude session that holds the whole sample library) which samples apply to the video you are checking and what exactly to compare.",
     "inputSchema": {"type": "object", "properties": {"question": {"type": "string"}, "run": {"type": "string"}, "video": {"type": "string"}, "model": {"type": "string"}, "mode": {"type": "string"},
                                                      "product": {"type": "string"}, "kinds": {"type": "array", "items": {"type": "string"}}, "checkpoints": {"type": "array", "items": {"type": "string"}}}, "required": ["question"]}},
    {"name": "flags_search", "description": "Search the recorded Higgsfield flags (needs a flags:read key). Same operators as the web app: status:open tag:real-person-face model:wan run:... asset:...",
     "inputSchema": {"type": "object", "properties": {"q": {"type": "string"}, "limit": {"type": "integer"}}}},
]


def text(s):
    return [{"type": "text", "text": s if isinstance(s, str) else json.dumps(s, ensure_ascii=False, indent=1)}]


def call_tool(name, a):
    if not lc.ensure(8):
        raise RuntimeError("Loupe is not running and could not be started")
    if name == "qc_search":
        qs = "&".join(f"{k}={v}" for k, v in (("model", a.get("model", "")), ("mode", a.get("mode", "")), ("kinds", ",".join(a.get("kinds", []))), ("product", a.get("product", "")),
                                                ("q", a.get("q", "")), ("limit", a.get("limit", 8))) if v != "")
        s, d = lc.call("GET", "/api/qc/samples?" + qs.replace(" ", "%20"), key_name="qc")
        return text(d) if s == 200 else _err(s, d)
    if name == "qc_get_sample":
        sid = str(a.get("id", "")).upper().lstrip("S")
        s, d = lc.call("GET", f"/api/qc/samples/S{sid}", key_name="qc")
        if s != 200:
            return _err(s, d)
        s2, img = lc.call("GET", f"/api/qc/samples/S{sid}/image", key_name="qc", raw=True)
        out = text(d["sample"])
        if s2 == 200:
            out.append({"type": "image", "data": base64.b64encode(img).decode("ascii"), "mimeType": "image/png"})
        return out
    if name == "qc_brief":
        s, d = lc.call("POST", "/api/qc/brief", a, key_name="qc")
        return text(d.get("text", d)) if s == 200 else _err(s, d)
    if name == "qc_consult":
        s, d = lc.call("POST", "/api/qc/consult", a, key_name="qc", timeout=170)
        return text(d) if s == 200 else _err(s, d)
    if name == "flags_search":
        s, d = lc.call("GET", "/api/flags?limit=%d&q=%s" % (int(a.get("limit", 20)), (a.get("q", "") or "").replace(" ", "%20")), key_name="qc")
        if s != 200:
            return _err(s, d)
        return text([{"id": e["id"], "status": e["status"], "title": e["title"], "kind": e.get("kind"), "model": (e.get("where") or {}).get("model"),
                      "tags": (e.get("trigger") or {}).get("tags"), "trigger": (e.get("trigger") or {}).get("text")} for e in d["entries"]])
    raise RuntimeError("unknown tool " + name)


def _err(s, d):
    raise RuntimeError(f"Loupe answered {s}: {d.get('error') if isinstance(d, dict) else d}")


def send(o):
    sys.stdout.write(json.dumps(o, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def main():
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            m = json.loads(line)
        except ValueError:
            continue
        mid, method = m.get("id"), m.get("method", "")
        if mid is None:        # a notification
            continue
        try:
            if method == "initialize":
                send({"jsonrpc": "2.0", "id": mid, "result": {"protocolVersion": (m.get("params") or {}).get("protocolVersion", "2024-11-05"),
                                                              "capabilities": {"tools": {}}, "serverInfo": {"name": "loupe", "version": "1.0"}}})
            elif method == "tools/list":
                send({"jsonrpc": "2.0", "id": mid, "result": {"tools": TOOLS}})
            elif method == "tools/call":
                p = m.get("params") or {}
                try:
                    send({"jsonrpc": "2.0", "id": mid, "result": {"content": call_tool(p.get("name", ""), p.get("arguments") or {}), "isError": False}})
                except Exception as e:  # noqa: BLE001
                    send({"jsonrpc": "2.0", "id": mid, "result": {"content": text(str(e)), "isError": True}})
            elif method == "ping":
                send({"jsonrpc": "2.0", "id": mid, "result": {}})
            else:
                send({"jsonrpc": "2.0", "id": mid, "error": {"code": -32601, "message": "method not found"}})
        except Exception as e:  # noqa: BLE001
            send({"jsonrpc": "2.0", "id": mid, "error": {"code": -32603, "message": str(e)}})


if __name__ == "__main__":
    main()
