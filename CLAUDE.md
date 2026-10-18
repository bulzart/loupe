# Loupe: working rules

Loupe is a local web app for the video pipelines /analyze-then-pick and /analyze-then-generate (Python standard library + SQLite + Pillow + ffmpeg, vanilla-JS UI): Higgsfield flags, generation-miss samples and a read-only QC keeper. `README.md` is the operating guide. This folder is its own git repository; `origin` is the private GitHub repo `bulzart/loupe`.

## Always commit changes

- Commit every change in this repo as soon as it is done and tested. One commit per logical change. Never end a task or a session with uncommitted edits.
- Write a descriptive message (what changed and why, never just "update"). When Claude made the change, end the message with `Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>`.
- After committing, push to `origin` (`git push`) so GitHub always holds the latest. Never force-push.
- Check `git status` before committing. Never commit `data/` (the database, API keys, the admin passphrase hash, caches, uploads) or anything secret. `.gitignore` keeps `data/` out; do not weaken it.
- Run the tests first and commit when they pass: `python tests/selftest.py` (add `--keeper` for the live keeper checks), `node tests/transport_check.js`, `node tests/page_smoke.js`. If a failure is known and accepted, say so in the commit message.

## Keep the docs in step

After every edit to Loupe (code, routes, UI, hooks, keys, the `atp.py` and `atg.py` commands that call it), update in the same sitting:

- `README.md` in this folder.
- The build prompt `C:\Users\Glaresquare1\Documents\prompts\loupe.txt` (outside this repo): copy it to `loupe.txt.bak-<date>-<why>` first, keep CRLF prose with no headings, keep every sentence true to the code.

## Running and restarting

`python loupe.py serve` (or `python ~/.claude/analyze-then-pick/scripts/atp.py loupe ensure`, or the same command in `analyze-then-generate/scripts/atg.py`) serves http://127.0.0.1:8787, localhost only. Restart it after changing Python code; `web/*.js` and the CSS are served live. A restart is: stop the process listening on port 8787, then run `atp.py loupe ensure`.

## Conventions

- Many files use CRLF line endings: preserve each file's own endings when editing.
- No niche, brand or product names in code: they are data that the pipeline's agents collect and decide. Scripts gather and record; the agents decide.
- No `innerHTML` anywhere in the UI: every value reaches the page as a text node or an attribute.
- Never leave backup copies inside this folder.
