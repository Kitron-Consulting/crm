# crm

A lightweight CLI pipeline tracker for solo consultants and freelancers. No dependencies, no database, no bullshit.

Your contacts live in a JSON file. You manage them from the terminal.

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

Verify the install: `crm --version`

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
crm thread [QUERY]         Browse recent email thread with a contact
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

Add an SMTP config manually to `crm_data.json`:

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

Most providers don't auto-save SMTP-sent emails (Gmail does, Fastmail/Office365/custom domains usually don't).

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

**Security note:** SMTP password is stored plaintext in `crm_data.json`. Keep that file private (`chmod 600`).

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

# Use a different data file (--data flag or CRM_DATA env var)
crm --data clients.json list
crm --data leads.json due
CRM_DATA=~/leads.json crm due
```

## Why this exists

Most CRMs are overkill for a solo practice. You don't need dashboards, integrations, or a monthly fee. You need to know who to follow up with tomorrow.

## Author

Eemil Kiviahde — [Kitron Consulting](https://kitron.dev)

## License

MIT
