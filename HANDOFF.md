# Handoff — web UI + mail work (branch `feature/web-ui`)

Snapshot for continuing on another machine. Delete this file before the eventual
v1.9.0 merge to `main`.

## ⚠️ Storage is now SQLite (big-bang migration — done)
The JSON-blob store is gone. The store is a single **SQLite `.db` file**
(`crm/db.py` = the `Db` DAL; `crm/storage` = blob-sync backends). `load_data`/
`save_data` are replaced by `storage.open_db()` / `push_db(db)` / `close_db(db)`
sessions; all CLI commands + the web API use `db.*` queries. Contacts have
**stable integer PKs** now (id=1-based, not list position). Config is a kv table.
- **S3 unchanged in spirit**: the `.db` is pushed/pulled whole with the same
  `If-Match` ETag concurrency. A legacy JSON blob **auto-upgrades in place** on
  first open (magic-byte sniff → migrate → SQLite bytes to the same key; a
  `.jsonbak` backup is kept locally). So your real S3 data upgrades itself the
  first time any device writes — **nothing to run**, but the first write is the
  migration; let it happen from one device before hammering others.
- `sqlite3` is stdlib → no new runtime dep. `msal` is now declared in pyproject.
- Tests: `pytest -q` → **161 green**. New: `test_db.py`, `test_storage.py`,
  `test_meetings.py`. `test_web.py`/`test_import.py`/etc. rewritten onto the DAL.

## "Next meeting" on cards — done (one live-mailbox caveat)
Contact cards (Board) show a blue **meeting chip**; the drawer Details tab shows
a **Next meeting** block with a Join link. Backed by a synced `meetings` table
(`db.set_meetings`/`next_meetings`), populated by `mail.fetch_upcoming_meetings`
(scans Inbox+Sent, correlates by counterparty email). A **"Sync meetings"**
button on the Board calls `POST /api/meetings/refresh`.
- ⚠️ **The scan is the ONE thing not tested against a real mailbox** — I never
  touch live data. The pure folding + DAL + endpoint are unit-tested (mocked
  IMAP); the actual `fetch_upcoming_meetings` IMAP walk needs a real session.
  Click "Sync meetings" against your Exchange box and sanity-check the results
  (counterparty attribution, dedupe, future-only) before trusting it.

## How to continue on the laptop
```bash
git fetch && git checkout feature/web-ui

# Python (CLI + server): 3.13 venv, install with uv
uv venv --python python3.13 .venv
uv pip install --python .venv/bin/python -e . pytest   # msal now in pyproject
.venv/bin/python -m pytest -q            # expect 161 green

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

## Timeline view — polished & verified
The backend is complete and tested (`stage_segments`/`stage_history` in
`crm/stages.py`, exposed as `segments`/`stage_history` on `/api/state`). The
frontend (`web/src/views/Timeline.svelte`) now has the full polish pass:
- **rich pointer-following hover card** (`.tl-card`) replacing native `title`
  on bars — stage, current/past tag, contact subline, date range, duration
  (days + weeks), and a "stuck ≥30d" flag; clears on pointer-leave.
- **week (Monday) gridlines** at the 1M and 3M ranges only (`weekLines`); hidden
  at 6M/12M/All where they'd be noise.
- **per-stage duration summary** strip (`.tl-summary`): one tinted chip per
  stage with current occupancy count and median days-in-stage, in config order.
- sort options reviewed (longest / recently moved / name / stage order) — all fine.

Verified with a real Playwright suite: `scratchpad/pw/timeline.js` (19 assertions,
0 console errors, light+dark screenshots). See "Running the Timeline suite" below.
Builds clean (no a11y warnings) and `pytest` still 134 green.

## App-shell scroll fix (all views)
Fixed a double-scrollbar / vertical-overflow bug that hit most views. The shell
is now locked to the viewport (`.app`/`.main` are `height:100vh`) and `<main>` is
the single scroll container (`display:flex; flex-direction:column; overflow:auto`),
so the page body never scrolls and there's never a second scrollbar. The old inner
scrollers used guessed caps (`.table-wrap max-height:calc(100vh-230px)`, `.tl-rows`
`…-260px`) that didn't match real chrome height and leaked ~13–58px of body scroll;
they're now `flex:0 1 auto; min-height:0; overflow:auto` (size to content, shrink +
scroll internally when they outgrow the viewport). Their sticky headers (`th`,
`.tl-axis`) pin to the padding-less card top, not under the topbar's padding.
Board keeps its own `.main.fill main { overflow:hidden }` column-scroll. Verified 0
body/horizontal overflow and 0 nested scrollers across board/contacts/due/import/
calendar/timeline at viewport heights 640/760/900.

## TODO / not done
- **Restart `crm serve`** after pulling: the IMAP timeouts, the no-device-flow-
  in-server guard, outbound-invite parsing, and thread time-correlation are
  Python and need a restart (a running server predating them won't have them).
- ~~Finish Timeline~~ — done (see above).
- **"Next meeting" line on contact cards** — deferred; needs thread events per
  contact, which today only exist once a thread is opened.
- **Release**: this is a big v1.9.0 candidate. Nothing has been tested against
  the real Exchange mailbox end-to-end by me (I never touch live data) — give it
  a real session (an invite, a PDF, a large import) before tagging. Then commit
  to `main` + tag `v1.9.0` (CI builds the UI into the wheel/binary).
- **kitron repo**: `letter-generator.py` output-clobbering + overflow fixes are
  still uncommitted there (separate repo).

## Running the Timeline suite (`scratchpad/pw/timeline.js`)
```bash
.venv/bin/python scratchpad/make_seed.py            # regenerate scratchpad/seed_ui.json (18 contacts)
cd web && npm run build && cd ..                     # bundle the current UI
.venv/bin/python -m crm --data scratchpad/seed_ui.json serve --port 8790 --no-browser
# ...copy the ?t=<token> it prints, then in another shell:
cd scratchpad/pw && npm i playwright                 # once
CHROME_BIN=~/.cache/ms-playwright/cft-1243/chrome-linux64/chrome \
  node timeline.js "http://127.0.0.1:8790/?t=<token>"
```
Heads-up: `npx playwright install chromium` **fails on this network** — the
Playwright CDN mishandles its own 307 redirect and times out at 30s. Workaround
used here: `curl -sSL <cft-zip-url> -o /tmp/cft.zip` (curl follows the redirect
fine, ~17s), unzip, and point the suite at the binary via `CHROME_BIN`. The
Chrome-for-Testing build already sits at `~/.cache/ms-playwright/cft-1243/`.
`scratchpad/` is treated as disposable (holds node_modules + the browser + PNGs,
so it's *not* committed) — `make_seed.py` and `pw/timeline.js` live there and
won't transfer to the laptop. Recreate them from the steps above, or move the two
source files into the repo (e.g. `web/e2e/`) if you want them tracked for CI.

## Gotchas / conventions
- Package installs via **uv**, never bare pip (`uv pip install --python .venv/bin/python ...`).
- Never run `crm serve` against real data on a test port — always `--data <seed>`;
  seed at `scratchpad/seed_ui.json` (session-local, won't transfer — regenerate).
- `pkill -f <pattern>` matches its own shell; kill servers by the PID that owns
  the port (`ss -ltnp | grep :PORT`).
- Svelte 5.57: `svelte-ignore` only honours the FIRST code in a space-separated
  list — use commas.
