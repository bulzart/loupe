# Loupe

The web app of the `/analyze-then-pick` pipeline. It does three things:

1. **Flag record.** Every prompt and video Higgsfield flagged, refused, withheld or rerouted, with the evidence the pipeline could
   gather (the request, each input with its file, thumbnail and file facts, the same file in earlier runs, the raw answer the
   hook saved). It replaces the old `caught_flags.html` page.
2. **Generation misses.** One page, no second step: you pick a run and a video, the wrong and the right video play side by side, and you
   draw an arrow, circle, box or note straight on either of them (red on the wrong one, green on the right one; the same mark
   appears on both with the same place and size, and Undo / Redo / Ctrl+Z / Ctrl+Y work). Type what the miss was,
   press **Mark this moment** (it saves at once), and Loupe saves one image: the miss on the left, the correct frame of the same moment on the right, both with
   your marks, a zoom of the marked place, and your explanation burned in at the bottom centre. A miss does not need a right version:
   pick "No right version" and it stays as a miss example (the right panel says so, only the miss is marked). Attach a right version
   later and the saved moments are rebuilt with it (open Edit on each to line the right video up).
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

- **CLI (primary):** `atp.py caught-flag add|set|list|reconcile|open`, `atp.py qc-consult`, `atp.py qc-brief`, `atp.py plan-hints`, `atp.py loupe ensure|status|open`.
- **MCP (optional):** `mcp_server.py` offers `qc_search`, `qc_get_sample` (text + the image), `qc_brief`, `qc_consult`, `flags_search`.
  Register once with `claude mcp add --scope user loupe -- python C:/Users/Glaresquare1/.claude/loupe/mcp_server.py`
  (it reads `data/keys/qc.key`, or `LOUPE_API_KEY` if you pass `-e LOUPE_API_KEY=<key>`).
- **HTTP:** the same JSON API the web page uses (`/api/...`, listed in `loupe_app/server.py`).

## The keeper

`qc.py` starts `claude -p … --model claude-haiku-5-5 --tools Read --strict-mcp-config` as a session of its own (`--session-id`, then
`--resume`), working in `data/keeper`, told the whole library digest. It can read the sample images and write nothing. After 30
turns it is replaced by a fresh session rebuilt from the library (the library is the memory, so nothing is lost). If it cannot run,
`qc-consult` falls back to the deterministic brief (`mode: fallback`). A consult costs about $0.001.

## Planning hints (the keeper's second job)

The first request of each video asks the keeper once, in text only, which past misses could matter for its plan (`atp.py plan-hints --beats …`).
The keeper is told to be sparse: the default answer is no hints, a hint needs a beat of that plan that matches the sample's trigger and a
compatible model, at most three, each a risk plus a positive lever (what the right version shows and which of the writer's own tools to use at
that beat), never a wording to paste. Loupe enforces the same in code (real ids, high or medium confidence, model family must fit unless the
miss is model-independent, three at most, short fields, no images, nothing at all when the keeper is unavailable). The writer's rules are in the
`atp-write-request` skill: apply only to the matching beat, smallest change, positive wording, at most two per video, record the decisions.

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
