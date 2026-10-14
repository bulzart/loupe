# Loupe

The web app of the `/analyze-then-pick` pipeline. It does three things:

1. **Flag record.** Every prompt and video Higgsfield flagged, refused, withheld or rerouted, with the evidence the pipeline could
   gather (the request, each input with its file, thumbnail and file facts, the same file in earlier runs, the raw answer the
   hook saved). It replaces the old `caught_flags.html` page.
2. **Generation misses.** One page, no second step: you pick a run and a video, the wrong and the fixed video play side by side, and you
   draw an arrow, circle, box or note straight on either of them (red on the wrong one, green on the fixed one). Type what the miss was,
   press **Mark this moment** (it saves at once), and Loupe saves one image: the miss on the left, the correct frame of the same moment on the right, both with
   your marks, a zoom of the marked place, and your explanation burned in at the bottom centre.
3. **QC samples.** The QC phase of a run (and only that phase) reads those images. A separate read-only Claude session, the *keeper*,
   holds the whole library and answers QC agents' questions about it; it never edits anything.

Standard library Python + SQLite + Pillow + ffmpeg. The UI is plain JavaScript (no innerHTML). It listens on `127.0.0.1:8787` only.

## Start

```
python C:/Users/Glaresquare1/.claude/loupe/loupe.py serve         # http://127.0.0.1:8787
```

`atp.py loupe ensure` starts it detached when a pipeline command needs it. The first time you open the page it asks you to **set
the admin passphrase yourself** (8+ characters). Agents never get that passphrase.

## Who may do what

| Who | How | Scopes |
|---|---|---|
| You | passphrase login (12 h session cookie, CSRF header, Host check, 5 wrong tries lock the login for a minute) | everything |
| The pipeline (`atp.py`, hooks) | `Authorization: Bearer lk_…` from `data/keys/pipeline.key` | `flags:write`, `flags:read`, `runs:read` |
| The QC phase (`atp.py qc-consult`, MCP) | `data/keys/qc.key` | `qc:read`, `flags:read` |

Keys are shown once, stored hashed, revocable, with a last-used time and a use counter; logins, key changes, flag and miss edits, settings
and uploads go to the audit log (Settings page); consults are logged on the Keeper page. Make more with `python loupe.py key-create --name N --scopes a,b [--file NAME]`.

## How agents reach it

- **CLI (primary):** `atp.py caught-flag add|set|list|reconcile|open`, `atp.py qc-consult`, `atp.py qc-brief`, `atp.py loupe ensure|status|open`.
- **MCP (optional):** `mcp_server.py` offers `qc_search`, `qc_get_sample` (text + the image), `qc_brief`, `qc_consult`, `flags_search`.
  Register once with `claude mcp add --scope user loupe -- python C:/Users/Glaresquare1/.claude/loupe/mcp_server.py`
  (it reads `data/keys/qc.key`, or `LOUPE_API_KEY` if you pass `-e LOUPE_API_KEY=<key>`).
- **HTTP:** the same JSON API the web page uses (`/api/...`, listed in `loupe_app/server.py`).

## The keeper

`qc.py` starts `claude -p … --model claude-haiku-5-5 --tools Read --strict-mcp-config` as a session of its own (`--session-id`, then
`--resume`), working in `data/keeper`, told the whole library digest. It can read the sample images and write nothing. After 30
turns it is replaced by a fresh session rebuilt from the library (the library is the memory, so nothing is lost). If it cannot run,
`qc-consult` falls back to the deterministic brief (`mode: fallback`). A consult costs about $0.001.

## Hooks (in `~/.claude/settings.json`, scripts in `~/.claude/hooks/`)

- `atp_flag_watch.py` — PostToolUse and PostToolUseFailure of the Higgsfield generation tools: a flag-like answer is saved to Loupe as a
  *capture* the moment it returns; the agent gets a one-line reminder to record the flag before changing the request.
  Loupe down: the capture waits in `data/outbox/`.
- `atp_resume_note.py` — SessionStart after a compaction or resume: names the run in progress and its `next_skill`.
- `atp_read_guard.py` — PreToolUse of Read: refuses a whole-file read of `atp.py`, `models/index.json`, the catalog snapshot and the
  backups (a read with offset/limit passes).

## Data (not in git)

`data/loupe.db` (SQLite), `data/samples/<id>/{wrong,fixed,annotated,composite,zoom}.png`, `data/flags/` (thumbnails), `data/captures/`,
`data/keys/`, `data/keeper/`, `data/cache/`, `data/outbox/`. Back up the `data` folder to keep the record.

## Tests

`python tests/selftest.py` (36 checks; `--keeper` adds 3 that call the real keeper) and `python tests/demo.py` (a demo instance on port
8799 with throwaway data, passphrase `demo-passphrase`).

## Build prompt

`C:/Users/Glaresquare1/Documents/prompts/loupe.txt` describes the whole app; it is updated after every edit to Loupe.
