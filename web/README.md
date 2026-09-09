# crm web UI

Svelte 5 + Vite frontend for `crm serve`. It builds to **one** self-contained
file, `crm/static/index.html` (JS + CSS inlined via `vite-plugin-singlefile`),
which the Python server reads per request at `GET /`. The build output is
gitignored and produced at release time.

## Dev

```sh
# 1. start the API server (prints a URL with ?t=<token>)
cd .. && .venv/bin/python -m crm serve --no-browser

# 2. in another shell, start Vite (proxies /api to 127.0.0.1:8765)
cd web && npm install && npm run dev

# 3. open http://localhost:5173/?t=<token>
```

## Build

```sh
cd web && npm run build      # -> ../crm/static/index.html (single file)
```

Then `crm serve` picks it up immediately; no restart needed.

## Layout

```
src/
  main.js                  mount
  app.css                  global stylesheet (design tokens, light/dark)
  App.svelte               shell: sidebar, top bar/search, view switch, shortcuts
  lib/api.js               fetch wrapper, X-CRM-Token header
  lib/store.svelte.js      server state (S), UI state (UI), helpers, loadState/mutate
  lib/import.svelte.js     Import view state (survives view switches)
  lib/modal.svelte.js      confirm dialog + new-contact modal state
  lib/toast.svelte.js      toast queue
  views/                   Board, Contacts, Due, Import
  drawer/                  Drawer + Next/Fields/Notes/Thread blocks
  components/              Icon, Btn, Select, chips, FiltersBar, Modal, ...
```
