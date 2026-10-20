# Loupe

The web app of the video pipelines (`/analyze-then-pick`, `/analyze-then-generate` and `/analyze-with-loupe`; the runs of all three can be browsed here). It does three things:

1. **Flag record.** Every prompt and video Higgsfield flagged, refused, withheld or rerouted, with the evidence the pipeline could
   gather (the request, each input with its file, thumbnail and file facts, the same file in earlier runs, the raw answer the
   hook saved). It replaces the old `caught_flags.html` page.
2. **Generation misses.** One page, no second step: you pick a run and a video, the frames play side by side (two to start with: the miss from Video A and the right version from Video B, on the same time; choosing the right version later, even after marks were drawn on the first frame, adds its frame once at the same time, and the marks stay on the frame they were drawn on), and you
   draw an arrow, circle, box or note straight on any of them (red on a frame you call Wrong, green on a frame you call Right). You choose what each
   frame is: Wrong or Right, which video it comes from (so an early frame of one video can be the right one), and its own time (slider, 1-frame buttons or a time box); **+ Add a frame** adds as many as you need.
   Nothing is marked or copied for you. Undo / Redo / Ctrl+Z / Ctrl+Y work. Everything is optional: a right version, a wrong frame, even a video or any frame (a report can be in words only: the idea, what could have been better, what the AI agents missed or could not understand).
   Type what the miss was or what the scenario is about,
   press **Mark this moment** (it saves at once), and Loupe saves one image: each frame
   tagged WRONG or RIGHT with its time and your marks, a zoom around each frame's own marks, and your explanation burned in at the bottom. The bottom band is split in half: the explanation on
   the left and, on the right, the **product sample the video used**, found automatically from the run (its request, product registry and asset
   inventory) as the original file in the client's reference folder. The report page shows what it found and lets you switch it off. A miss does not need a right version:
   the page never guesses the right version: you pick it, or pick "No right version exists", before a moment can be saved, and then the miss stays as a miss example (the right panel says so, only the miss is marked). Attach a right version
   later and the saved moments are rebuilt with it (open Edit on each to line the right video up).
3. **QC samples.** The QC phase of a run (and only that phase) reads those images. A separate read-only Claude session, the *keeper*,
   holds the whole library and answers QC agents' questions about it; it never edits anything.

Standard library Python + SQLite + Pillow + ffmpeg. The UI is plain JavaScript (no innerHTML). It listens on `127.0.0.1:8787` only.

## Start

```
python C:/Users/Glaresquare1/.claude/loupe/loupe.py serve         # http://127.0.0.1:8787
```

`atp.py loupe ensure` (or `atg.py loupe ensure`) starts it detached when a pipeline command needs it. The first time you open the page it asks you to **set
the admin passphrase yourself** (8+ characters). Agents never get that passphrase.

## Who may do what

| Who | How | Scopes |
|---|---|---|
| You | passphrase login (12 h session cookie, CSRF header, Host check, 5 wrong tries lock the login for a minute) | everything |
| The pipeline (`atp.py`, `atg.py`, hooks) | `Authorization: Bearer lk_…` from `data/keys/pipeline.key` | `flags:write`, `flags:read`, `runs:read` |
| The QC phase (`qc-consult` of either script, MCP) | `data/keys/qc.key` | `qc:read`, `flags:read` |

Keys are shown once, stored hashed, revocable, with a last-used time and a use counter; logins, key changes, flag and miss edits, settings
and uploads go to the audit log (Settings page); consults are logged on the Keeper page. Make more with `python loupe.py key-create --name N --scopes a,b [--file NAME]`.

## How agents reach it

- **CLI (primary):** `caught-flag add|set|list|reconcile|open`, `qc-consult`, `qc-brief`, `plan-hints` and `loupe ensure|status|open`, with the same arguments in `atp.py`, `atg.py` and `awl.py` (each writes the runs of its own library). `awl.py` also reads the keeper's state (`GET /api/qc/keeper/state`, qc key) in its `status` and `doctor`, and uploads from a capture (`hf-put --capture`).
- **MCP (optional):** `mcp_server.py` offers `qc_search`, `qc_get_sample` (text + the image), `qc_brief`, `qc_consult`, `flags_search`.
  Register once with `claude mcp add --scope user loupe -- python C:/Users/Glaresquare1/.claude/loupe/mcp_server.py`
  (it reads `data/keys/qc.key`, or `LOUPE_API_KEY` if you pass `-e LOUPE_API_KEY=<key>`).
- **HTTP:** the same JSON API the web page uses (`/api/...`, listed in `loupe_app/server.py`).

**Kinds suggested, confirmed by the admin.** `GET /api/misses/suggest-kinds?text=…` returns the kinds of the vocabulary whose own words occur in a text (a prefix match on the vocabulary's words, nothing weighed); the report form's button "Suggest kinds from the words" ticks them and the admin unticks what does not fit; nothing is saved without the admin.

**The composite says what its tags mean.** When the frames come from two videos, the bottom band opens with a legend (frames tagged "as generated" are from video A, "correct version" from video B, a different video); when the admin's marks cover more than 60 % of the frame, no zoom row is drawn (it would repeat the panel) and the band says so. Existing samples keep their images until they are regenerated.

## The keeper

`qc.py` starts `claude -p … --model claude-haiku-5-5 --tools Read --strict-mcp-config` as a session of its own (`--session-id`, then
`--resume`), working in `data/keeper`, told the whole library digest. It can read the sample images and write nothing. After 30
turns it is replaced by a fresh session rebuilt from the library (the library is the memory, so nothing is lost). If it cannot run,
`qc-consult` falls back to the deterministic brief (`mode: fallback`). A consult costs about 1 to 4 cents (it grows with the library and with the session's length); each consult's cost is kept in the `consults` table of the database.

**What an answer carries (since 2026-10-20).** Every `relevant[]` item names the sample's `title` (the admin's words), `details`, `correct_note`, `idea`, `better`, `missed`, where it came from (`model`, `run`, `video`, `t_wrong`, `kinds`), the composite's path and the keeper's `why`, `check` and `wrong_pattern`. Every field is whole or cut at a sentence end with an ellipsis (`_clip`), never mid-word; `needs_unmet` lists only what Loupe could not fetch, and its `more` item names the kinds and times the keeper still wanted. A run that is not under Loupe's run folders is named as such in the fetch reason.

**The prompts of the two takes (since 2026-10-20).** Beside Video A and Video B the report form keeps the prompt that produced the wrong take and the prompt of the corrected take (each filled from the run's request file of the attempt the version picked belongs to, or pasted) and one line of the admin's own words on what in the prompt made the difference. They live in the `misses` table only (`wrong_prompt`, `fixed_prompt`, `prompt_note`; the seed carries them): never in the composite, its meta or the keeper's digest, and nothing compares or diffs them in code. The keeper reads both whole when it asks for `sample_origin` (4000 characters each, as evidence for that one answer) and judges itself what the corrected prompt did differently; a pipeline agent reads them through `GET /api/qc/samples/S<id>?prompts=1` (`awl.py sample-prompts`, written to a file of the run) for a sample it judged relevant. Every list, brief, consult answer and hint carries only `prompts` (`pair`, `wrong`, `fixed` or empty) and `prompt_note`, so the keeper's memory and the agents' context stay small; a change to the prompts changes the library version, so the keeper is re-synced and re-reads them on request.

**The versions of a video (since 2026-10-20).** `GET /api/runs/<run>` serves each video's files with a `role`: `generated` (the video as the service returned it: `raw.mp4` of an attempt folder `60-outputs/video-NN/aK/`, or the oldest file of an attempt folder that has none; the clean video and the preview are made from it), `processed` (conformed, trimmed, with the original audio, copies, previews, the loose files of `60-outputs/video-NN/`), `deliverable` or `client-fix`. A file of an attempt folder carries its `attempt`, and `delivered` is true for the attempt whose finished file is byte for byte the run's clean video (`clean.mp4` or `deliverables/video-NN-clean.mp4`; sizes are compared first, a hash is read only for equal sizes). The two pickers of the report form list only the generated takes (named like `a3 · generated at Higgsfield · delivered (its clean video and preview)`), the client's fixes, a file uploaded or typed, and whatever an opened report already uses; a tick under them lists every other file as well, and a video with no recognisable generated take lists every file as before. Video A starts on the first attempt's generated take. `GET /api/request-prompt` and the product sample read the attempt from the picked file's path: the attempt folder it sits in, else the attempt it is a copy of (so a clean video fills the prompt of the take it was made from, whatever its name says); a preview, or a deliverable, is the delivered take's when it is the run's own recorded clean or preview of that video (`state.json` `videos[].clean` / `preview`, `result.json`) or a byte-for-byte copy of one; a file whose attempt cannot be told fills nothing and the form says why (nothing is guessed, never "the latest attempt"); a label such as `a2 · raw.mp4` is read by its attempt word. A request file that is not a video call is never the prompt of an attempt: the request of a still asked for beside the video (a start frame, a guide plate) has a `tool` that is not a video call, or no duration and an image model's own parameter (a resolution tier such as 2k, a quality). The usage index labels such a request with its own tag (`g1`) or `image` instead of `a1`, and rebuilds itself once for every run after this change (`USAGE_INDEX_VERSION`).
**One call at a time, never stuck.** The keeper runs in its own process group and is ended whole when its call times out (on Windows `claude.cmd` starts node; killing only the shell left node holding the pipes and the lock, and one real consult waited 63 minutes). The lock around every keeper call is acquired with a timeout (`LOUPE_KEEPER_LOCK_WAIT`, default 120 s; a sync waits `LOUPE_KEEPER_SYNC_LOCK_WAIT`, 20 s): a consult that cannot get it answers from the brief with `mode: busy`, a sync reports `state: busy` and the watcher tries again. `GET /api/qc/keeper/state` (qc:read) returns in_sync, turns, the library version, the model and the sync times, without the consult log or the session id.

**Always current.** The keeper is not left to wake at the first question: `POST /api/qc/keeper/sync` (what `atp.py|atg.py loupe ensure` calls when a run is created, resumed or about to spawn its agents) boots it, or sends it the whole current library, when there is no session, the session is worn out or the library changed since its last digest, and does nothing (no model call) when it is current. The server also boots it at start-up and a watcher re-reads the library about 25 seconds after the admin's last edit (a run of edits costs one call), retrying a failure after five minutes. `GET /api/qc/keeper` and the Keeper page show `in_sync`. Each sync is in the audit log (`keeper.sync`, with its cost). A consult still checks the version itself, so a question never meets an outdated keeper.

**Asking instead of guessing.** The digest line of a sample says where it came from (run, video, time, mode) and leaves empty fields out. The keeper may put a `needs` list in its answer: `video_plan`, `request_prompt`, `product_registry`, `video_frame` (QC only, with a time), `sample_origin` (a sample id: its plan, request prompt and product image) or `ask_agent` (a question only the QC agent can answer). Loupe fetches what it can from the run named in the question, read-only and capped, copies it into `data/keeper/ctx/<consult>/` (the last 20 are kept), sends it to the same session and the keeper answers again; at most two rounds of three requests, each round one keeper turn. What cannot be fetched comes back as `needs_unmet`; the toolkits print an `unmet_rule` and the QC agent answers once with `qc-consult --note`. Fetched data is evidence for that one answer: it is never added to the library, the keeper is told so, and the library stays its only memory.

## Planning hints (the keeper's second job)

The first request of each video asks the keeper once, in text only, which past misses could matter for its plan (`plan-hints --beats …`).
The keeper is told to be sparse: the default answer is no hints, a hint needs a beat of that plan that matches the sample's trigger and a
compatible model, at most three, each a risk plus a positive lever (what the right version shows and which of the writer's own tools to use at
that beat), never a wording to paste. Loupe enforces the same in code (real ids, high or medium confidence, model family must fit unless the
miss is model-independent, three at most, fields cut at a sentence end (600 characters for the trigger and the outcome, 700 for the lever, 500 for the caveat: the condition that decides whether a hint applies stays whole), no images, nothing at all when the keeper is unavailable or busy). `held_back_why` says why each offered hint was set aside. The writer's rules are in the
`atp-write-request`, `atg-write-request` and `awl-write-request` skills: apply only to the matching beat, smallest change, positive wording, at most two per video, record the decisions.

The keeper matches by mechanism: both consult kinds carry the product's details (shape, size, opening, natural posture, from its registry row,
`--product-details`), a sample is relevant when it and the video share a product detail and a scene mechanism, and its answers are conditions
for this video, never "never/always" commands (Loupe also holds back a hint worded that way). Fill the report form's product field with the
product's details, not only its name, so samples can be matched this way.

## Hooks (in `~/.claude/settings.json`, scripts in `~/.claude/hooks/`)

- `atp_flag_watch.py` — PostToolUse and PostToolUseFailure of the Higgsfield generation tools: a flag-like answer is saved to Loupe as a
  *capture* the moment it returns; the agent gets a one-line reminder to record the flag before changing the request, naming the toolkit script of the run (in either library) that changed last.
  Loupe down: the capture waits in `data/outbox/`.
- `atp_resume_note.py` — SessionStart after a compaction or resume: names the run in progress (of either library, the one that changed last) and its `next_skill`.
- `atp_read_guard.py` — PreToolUse of Read: refuses a whole-file read of `atp.py`, `atg.py`, `models/index.json`, the catalog snapshots and the
  backups (a read with offset/limit passes).

## Runs

The run browser, the product sample finder, the usage index and the evidence for a flag read `runs/` of every library in `core.LIBS` (`ATP_HOME`, `ATG_HOME`, `AWL_HOME`; `ATP_HOME` default `~/.claude/analyze-then-pick`, and `ATG_HOME`, default `~/.claude/analyze-then-generate`); each run is listed with its library (`lib`: `atp` or `atg`) and the media of both are readable. A run name that exists in both libraries resolves to the one whose `state.json` changed last.

## Data: what travels with the repo and what never does

`data/samples/` (every sample's composite, frames and meta), `data/uploads/` (the right-version videos the admin uploaded), `data/flags/` (the flag evidence) and
`data/seed/` are tracked, so a clone opens with the whole sample library: `python loupe.py seed export` writes `data/seed/loupe-seed.db` (an online copy of the
database with the misses, moments, flags and flag inputs only; the sessions, API keys, settings with the passphrase hash and the keeper session, captures, audit,
usage and file indexes and the consult log are dropped, and signed URLs are scrubbed from the flag records) beside `seed.json` (what it holds). A data folder
with no `loupe.db` starts from the seed at the first `core.db()` (the server start, any command); the admin then sets a passphrase and creates the keys.
Run the export after the library changed and commit it with the samples. The misses' video paths in the seed are those of the exporting machine: the
sample images and the keeper need none of them, only a regenerate does.

## Data that never goes in

`data/loupe.db` (SQLite), `data/samples/<id>/{wrong,fixed,annotated,composite,zoom}.png`, `data/flags/` (thumbnails), `data/captures/`,
`data/keys/`, `data/keeper/`, `data/cache/`, `data/outbox/`. Back up the `data` folder to keep the record.

## Repository

This folder is its own git repository; `origin` is the private GitHub repo `bulzart/loupe`. `CLAUDE.md` holds the working rules for any session that edits it: commit every change with a descriptive message, push after committing, never commit `data/`, keep this README and the build prompt in step.

## Tests

`python tests/selftest.py` (113 checks; `--keeper` adds the live keeper checks), `node tests/transport_check.js` (the frame controls against fake videos), `node tests/page_smoke.js` (the whole report page against a fake DOM and API), `node tests/flags_smoke.js` (the Flags page), `python tests/keeper_sync.py` (81 checks: the keeper receives every change to the library, the answer's fields and reasons, the busy fallback, a hung subprocess ended, the composite's zoom rule, the prompts of the two takes read on request and never in the digest; including scenarios, Example frames, miss examples and marks, for QC and for planning; the database, the QC library, the digest and the brief agree; no model call) and `python tests/keeper_sync.py --live` (the same, read-only, on the real library: the images, what QC is served over HTTP and by each pipeline's `qc-brief`, and whether the keeper's remembered version is current) and `python tests/demo.py` (a demo instance on port
8799 with throwaway data, passphrase `demo-passphrase`).

## Build prompt

`C:/Users/Glaresquare1/Documents/prompts/loupe.txt` describes the whole app; it is updated after every edit to Loupe.
