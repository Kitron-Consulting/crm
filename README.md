# crm

A lightweight CLI pipeline tracker for solo consultants and freelancers. No dependencies, no database, no bullshit.

Your contacts live in a JSON file. You manage them from the terminal.

Because everything is a CLI command over a plain JSON file, it works naturally with coding agents like Claude Code or Aider — see [Using with coding agents](#using-with-coding-agents).

![demo](demo.gif)

![interactive demo](demo_interactive.gif)

## Install

Download the binary for your OS from the [latest release](https://github.com/Kitron-Consulting/crm/releases/latest):

```bash
# Linux x86_64
curl -L -o /usr/local/bin/crm \
  https://github.com/Kitron-Consulting/crm/releases/latest/download/crm-linux-x86_64
chmod +x /usr/local/bin/crm

# macOS (Apple silicon)
curl -L -o /usr/local/bin/crm \
  https://github.com/Kitron-Consulting/crm/releases/latest/download/crm-macos-arm64
chmod +x /usr/local/bin/crm
```

No Python install needed on the target machine — the binary bundles its own. macOS Gatekeeper will warn the first time; clear with `xattr -d com.apple.quarantine /usr/local/bin/crm`.

Verify the install: `crm --version`. Future upgrades: `crm update` (may need `sudo` depending on where you installed).

**From source** (Python 3.10+):

```bash
git clone https://github.com/Kitron-Consulting/crm.git
cd crm
pip install .          # registers `crm` in your PATH (pulls boto3 too)
# or, run without installing:
python -m crm
```

`pip install --no-deps .` skips `boto3` if you only need the local backend.

## Usage

```
crm list [STAGE]           List contacts by stage
crm due [DAYS]             What needs action (default: 7 days)
crm show [QUERY]           Contact details + history
crm note [QUERY] [TEXT]    Add a timestamped note
crm stage [QUERY] [STAGE]  Move to new stage
crm next [QUERY] [ACTION] [DATE]   Set next action
crm done [QUERY]           Mark current action as completed
crm followup [QUERY] [--template NAME] [--dry-run] [--to EMAIL] [--no-context]
                           Send a templated follow-up email
crm thread [QUERY] [--full] [--json]   Browse recent email thread with a contact
crm edit [QUERY] [--field value ...]   Edit contact
crm add contact [--name X ...]         Add new contact
crm add stage [NAME]       Add a stage
crm add source [NAME]      Add a source
crm add template [NAME]    Add/edit an email template
crm rm contact [QUERY] [-y]  Remove contact (soft delete)
crm rm stage [NAME]        Remove a stage (if empty)
crm rm source [NAME]       Remove a source (if unused)
crm rm template [NAME]     Remove an email template
crm restore [QUERY]        Restore a removed contact
crm search TERM            Search across everything
crm stages                 List stages
crm templates              List email templates
crm config [KEY] [VALUE]   Get/set config (e.g., timezone)
crm where                  Show the active storage backend (file path or s3:// URI)
crm update [--check]       Self-update from the latest GitHub release
crm --version              Print the installed version
crm help [COMMAND]         Show help for a command
```

All commands work interactively — if you skip arguments, you get a picker.

## Examples

```bash
# What's due this week?
crm due

# Show full pipeline
crm list

# Add a note
crm note acme "Called, left voicemail"

# Move to next stage
crm stage acme meeting

# Set follow-up
crm next acme "Send proposal" +3d

# Search notes and contacts
crm search pricing
```

## Stages

Default stages:

```
cold · contacted · responded · meeting · proposal · won · lost · dormant
```

Contacts can move between any stages freely.

Stages are configurable:

```bash
crm stages              # list current stages
crm add stage nurture   # add a stage
crm rm stage dormant    # remove (only if no contacts in it)
```

## Sources

Default sources: `cold`, `referral`, `inbound`. Configurable:

```bash
crm add source website   # add a source
crm rm source inbound    # remove (only if no contacts use it)
```

## Configuration

```bash
crm config              # show all config
crm config timezone     # get a value
crm config timezone UTC+02:00   # set a value
```

Timezone is auto-detected on first run. Config is stored in `crm_data.json`.

## Email templates and follow-ups

Add an SMTP config to `crm_data.json`. The easiest way — and the only
convenient one when your data lives in S3 — is `crm config edit`, which opens
the whole config block as JSON in `$EDITOR` and writes it back through
whatever backend is active:

```bash
crm config edit
```

Or edit the file directly. Either way the config looks like:

```json
"config": {
  "smtp": {
    "host": "smtp.gmail.com",
    "port": 587,
    "user": "you@example.com",
    "password": "app-password",
    "from_name": "Your Name"
  },
  "imap": {
    "host": "imap.gmail.com",
    "port": 993,
    "user": "you@example.com",
    "password": "app-password",
    "sent_folder": "Sent",
    "inbox_folder": "INBOX"
  }
}
```

The `imap` block is optional — if set:
- Sent emails are saved to your Sent folder so they show up in webmail
- `crm followup` shows recent exchange as context and warns if the contact replied after your last message
- `crm thread <query>` lets you browse full email history with a contact

Most providers don't auto-save SMTP-sent emails (Gmail and Exchange Online /
Microsoft 365 do; Fastmail/custom domains usually don't). When the sending
account auto-saves, `crm` skips the IMAP append so you don't get a duplicate
in Sent.

### Microsoft 365 / Exchange Online (OAuth)

Microsoft has disabled IMAP basic auth on Exchange Online and is phasing out
SMTP basic auth, so password login no longer works there. Use OAuth 2.0
(XOAUTH2) instead by setting `"auth": "oauth-ms"` on the `smtp` and/or `imap`
blocks — no password is stored:

```json
"config": {
  "smtp": {
    "auth": "oauth-ms",
    "host": "smtp.office365.com",
    "port": 587,
    "user": "you@yourtenant.com",
    "client_id": "<entra-app-client-id>",
    "tenant_id": "<entra-tenant-id>",
    "from_name": "Your Name"
  },
  "imap": {
    "auth": "oauth-ms",
    "host": "outlook.office365.com",
    "port": 993,
    "user": "you@yourtenant.com",
    "client_id": "<entra-app-client-id>",
    "tenant_id": "<entra-tenant-id>",
    "sent_folder": "Sent Items",
    "inbox_folder": "INBOX"
  }
}
```

`client_id` and `tenant_id` are non-secret; `smtp` and `imap` normally share
the same pair. Install the optional dependency:

```bash
pip install "crm[ms365]"   # pulls in msal
```

**One-time prerequisites** (an admin registers an app in Microsoft Entra ID):

- A **public client** app registration (public/native client flows enabled).
- Delegated permissions on the *Office 365 Exchange Online* API:
  `IMAP.AccessAsUser.All` and `SMTP.Send`, plus `offline_access` — all
  **admin-consented**.
- SMTP AUTH enabled for the mailbox.

The **first** mail command (`crm followup`, `crm thread`) prints a
`https://microsoft.com/devicelogin` URL and a code to stderr for a one-time
device login. After that the token is cached and refreshed silently — no more
prompts. Run that first command interactively; non-interactive runs (cron,
pipes) with no cached token fail fast with instructions rather than hanging.

Exchange Online auto-saves SMTP-submitted mail to **Sent Items**, so `crm`
does not append a second copy over IMAP for `oauth-ms` accounts.

Create a template (opens `$EDITOR`):

```bash
crm add template follow_up
```

Template format:
```
Subject: Following up — {company}

Hi {first_name},

Just wanted to check in about our conversation...
```

Supported placeholders: `{name}` `{first_name}` `{company}` `{role}` `{email}` `{phone}`

Send a follow-up:

```bash
crm followup acme                          # interactive (pick template, review, send)
crm followup acme --template follow_up     # specific template
crm followup acme --dry-run                # print without sending
crm followup acme --to you@example.com     # override recipient (testing)
```

The email opens in `$EDITOR` for review. Save = send. Empty = cancel. Sent emails are logged as notes.

### Importing contacts from your mailbox

If you've done outreach outside the CRM, `crm import` reads your **Sent** folder
over IMAP and pulls in the people you've emailed who aren't contacts yet:

```bash
crm import                       # review every new address interactively
crm import --dry-run             # just list who would be imported
crm import --days 30             # only mail from the last 30 days
crm import --stage contacted --source cold   # override the defaults
```

Each candidate is reviewed one by one — **a**dd / **e**dit fields / **s**kip
(this run) / **i**gnore (never show again) / **q**uit. Names come from the
message's display name, company is guessed from the email domain (blank for
gmail/outlook/etc.), and imported contacts default to stage `contacted`, source
`cold`. Already-known contacts, your own address, and role addresses
(`no-reply@`, `mailer-daemon@`, …) are skipped. Requires the `imap` block to be
configured.

**Ignore list.** Pressing **i** adds an address to `config.import_ignore` so it
never surfaces again — handy for colleagues, vendors, and one-off recipients.
The list persists in your config (so it syncs via S3). Entries can be an exact
address (`someone@vendor.com`) or a whole domain (`@vendor.com`); edit it by
hand any time with `crm config edit`.

**Security note:** For password auth, the SMTP/IMAP password is stored
plaintext in `crm_data.json` — keep that file private (`chmod 600`). For
`oauth-ms`, **no password is stored**; instead MSAL keeps a refresh-token
cache at `~/.config/kitron-crm/msal_cache.json`. That file is a bearer
credential — `crm` writes it `chmod 600`. Do not commit it or sync it (it
lives outside `crm_data.json` precisely so it never rides along to S3).

## Web UI (local)

Some tasks — triaging imported contacts, glancing at the whole pipeline — are
just nicer in a browser than a terminal. `crm serve` starts a small local web
app for them:

```bash
crm serve                 # opens http://127.0.0.1:8765 in your browser
crm serve --port 8790     # different port
crm serve --no-browser    # just start the server, don't open a window
```

It's **localhost-only** and **token-gated** (a random token in the URL guards
the API, so a stray web page can't drive your CRM). The Python side is stdlib
only, and it reuses your existing data backend and mail config — so your data
and OAuth token never leave the machine. Ctrl+C stops the server.

Screens:
- **Board** — kanban by stage; drag a card to change stage; filter by stage /
  source / overdue / no-next-action; global search (`/`).
- **Contacts** — sortable table over the same search and filters.
- **Due** — overdue, due in 7 days, and no-next-action, with one-click
  Done / Set next.
- **Calendar** — month grid of next actions: drag a contact to another day to
  reschedule, click a day to set a next action, drag an unscheduled contact
  onto a day to schedule it.
- **Timeline** — each contact's stage history as time bars (stuck deals and
  cycle times at a glance); hover for durations, click to open. Stage moves
  are recorded structurally (`stage_history`) from now on; older contacts are
  reconstructed from their "Stage: a → b" notes.
- **Contact drawer** — edit fields, notes timeline, next action (`+7d` dates),
  the email thread over IMAP (rich-text mail rendered as text, quoted history
  collapsed, attachments with in-app preview, calendar/Teams invites as
  cards), remove.
- **Import** — scan Sent, then add / edit inline / ignore candidates.

Shortcuts: `/` search · `n` new contact · `1`–`6` switch views · `Esc` close.

The frontend is a **Svelte 5 + Vite** app in `web/`, compiled to a single
self-contained `crm/static/index.html` (gitignored; the release workflow builds
it and it ships inside the wheel/binary). From a source checkout, build it once
with `cd web && npm install && npm run build`. For live-reload development run
`crm serve --no-browser`, then `cd web && npm run dev` and open the Vite URL
with your `?t=…` token appended — see `web/README.md`.

## Data

Default location: `~/.config/kitron-crm/crm_data.json`. Override with `CRM_DATA`:

```bash
export CRM_DATA=~/clients.json
```

**Multi-device via S3.** Set `CRM_STORAGE` to an `s3://` URI and put your AWS creds in `~/.aws/credentials`:

```bash
export CRM_STORAGE=s3://your-bucket/crm_data.json
export CRM_S3_ENDPOINT=https://your-s3-host    # only for S3-compatible (B2, Hetzner, MinIO, ...)
```

`~/.aws/credentials` format:

```ini
[default]
aws_access_key_id = AKIA...
aws_secret_access_key = ...
```

**Bucket setup:** make it private (no public-read ACL), enable server-side encryption (SSE-S3 or KMS), and enable versioning — you get a free undo history for accidental deletes or bad writes.

The S3 backend uses ETag conditional writes — concurrent edits from a second device produce a clear `data changed remotely` error instead of silently overwriting. Re-run the command to retry.

Backups: copy the JSON file, or rely on bucket versioning.

## Scripting

All commands work non-interactively when given full arguments:

```bash
# Add a contact without the form
crm add contact --name "John Doe" --email "john@co.com" --company "Co" --stage contacted

# Edit specific fields
crm edit acme --stage meeting --role "CTO"

# Remove without confirmation
crm rm contact acme -y

# Pipe-friendly — colors and interactive pickers are disabled when not a terminal
crm due 14 | grep overdue
crm list > pipeline.txt

# Read a full email thread without the curses viewer (full bodies to stdout)
crm thread acme --full
crm thread acme --json | jq '.[].subject'

# Use a different data file (--data flag or CRM_DATA env var)
crm --data clients.json list
crm --data leads.json due
CRM_DATA=~/leads.json crm due
```

## Using with coding agents

Every command is a CLI invocation and every piece of state is a plain JSON file. Coding agents that run shell commands — Claude Code, Aider, Codex CLI, etc. — can drive the whole tool without any special integration.

A few examples of what works:

    claude "add a note to acme that they passed on the proposal and move them to lost"

    claude "who is due for follow-up this week and what was the last thing I said to them?"

    claude "draft a follow-up email to everyone in the proposal stage I haven't contacted in 14 days"

The agent reads `crm help`, figures out the right commands, runs them, and reports back. No MCP server, no plugin, no API key beyond whatever your agent already uses.

The same property makes scripting from shell straightforward (see [Scripting](#scripting)) — agents are just one more consumer of the same interface.

## Why this exists

Most CRMs are overkill for a solo practice. You don't need dashboards, integrations, or a monthly fee. You need to know who to follow up with tomorrow.

## Author

Eemil Kiviahde — [Kitron Consulting](https://kitron.dev)

## License

MIT
