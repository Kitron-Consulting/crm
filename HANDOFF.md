# Handoff — web UI + mail work (branch `feature/web-ui`)

Snapshot for continuing on another machine. Delete this file before the eventual
v1.9.0 merge to `main`.

## How to continue on the laptop
```bash
git fetch && git checkout feature/web-ui

# Python (CLI + server): 3.13 venv, install with uv
uv venv --python python3.13 .venv
uv pip install --python .venv/bin/python -e . msal pytest
.venv/bin/python -m pytest -q            # expect all green

# Web UI: Node 22, build the single-file bundle the server serves
cd web && npm ci && npm run build        # -> crm/static/index.html (gitignored)
```
Run the app: `.venv/bin/python -m crm serve` (uses your S3 data + mailbox; opens
`http://127.0.0.1:8765`). Live-reload UI dev: `crm serve --no-browser`, then
`cd web && npm run dev`, open the Vite URL with your `?t=<token>` appended.

**Build vs reload:** the server reads `crm/static/index.html` per request, so a UI
change needs `npm run build` + browser reload — NOT a server restart. A Python
change (crm/*.py) needs a `crm serve` restart.

## What's on this branch (all since v1.8.1, uncommitted before now)
CLI: `crm import` + persistent ignore list, `crm config edit`, IMAP quoting/
batching/timeouts, `crm thread --mime`, readline-prompt ANSI fix, self-update
PyApp path fix. Mail: MS365 OAuth (already released), HTML→text rendering, quoted-
history splitting, attachments, calendar/Teams invite parsing (inbound+outbound,
thread-correlated times), stage_history recording. Web: full Svelte 5 + Vite app
(Board w/ drag-drop, Contacts, Due, Calendar, **Timeline**, contact Drawer with
notes/next-action/email-thread incl. attachment preview + meeting cards, Import
triage). CI: release workflow builds the bundle before the wheel.

Tests: `.venv/bin/python -m pytest -q` → 134 passing. `uvx ruff check --select F821 crm` clean.

## ⚠️ Timeline view is a FIRST CUT — finish it
The backend is complete and tested (`stage_segments`/`stage_history` in
`crm/stages.py`, exposed as `segments`/`stage_history` on `/api/state`). The
frontend (`web/src/views/Timeline.svelte`) was written by hand during this
handoff because the sub-agent that was building it hit a model rate limit
mid-task. It renders (rows, stage bars, today line, month ticks, range 1M–All,
sort, click→drawer, stuck badge) and builds clean, but it's minimal. **Polish
remaining** (see the original spec I gave the agent):
- richer hover card instead of native `title` tooltips
- week gridlines at short ranges; nicer axis
- per-stage duration summary; verify sort options feel right
- the sub-agent's own Playwright suite for it was never written — add assertions
  (mirror `scratchpad/pw/calendar.js` / `timeline.js`)

Everything else in the app has agent-run Playwright verification; Timeline has
only the quick smoke I ran (18 rows, 20 bars, today line, click opens drawer, 0
console errors, light+dark screenshots in the session scratchpad).

## TODO / not done
- **Restart `crm serve`** after pulling: the IMAP timeouts, the no-device-flow-
  in-server guard, outbound-invite parsing, and thread time-correlation are
  Python and need a restart (a running server predating them won't have them).
- **Finish Timeline** (above).
- **"Next meeting" line on contact cards** — deferred; needs thread events per
  contact, which today only exist once a thread is opened.
- **Release**: this is a big v1.9.0 candidate. Nothing has been tested against
  the real Exchange mailbox end-to-end by me (I never touch live data) — give it
  a real session (an invite, a PDF, a large import) before tagging. Then commit
  to `main` + tag `v1.9.0` (CI builds the UI into the wheel/binary).
- **kitron repo**: `letter-generator.py` output-clobbering + overflow fixes are
  still uncommitted there (separate repo).

## Gotchas / conventions
- Package installs via **uv**, never bare pip (`uv pip install --python .venv/bin/python ...`).
- Never run `crm serve` against real data on a test port — always `--data <seed>`;
  seed at `scratchpad/seed_ui.json` (session-local, won't transfer — regenerate).
- `pkill -f <pattern>` matches its own shell; kill servers by the PID that owns
  the port (`ss -ltnp | grep :PORT`).
- Svelte 5.57: `svelte-ignore` only honours the FIRST code in a space-separated
  list — use commas.
