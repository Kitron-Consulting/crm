# crm

A lightweight CLI pipeline tracker for solo consultants and freelancers. No dependencies, no database, no bullshit.

Your contacts live in a JSON file. You manage them from the terminal.

## Install

```bash
# Clone and add to PATH
git clone https://github.com/Kitron-Consulting/crm.git
cd crm
chmod +x crm

# Option A: symlink to somewhere in your PATH
ln -s $(pwd)/crm ~/.local/bin/crm

# Option B: add to PATH in your shell rc
echo 'export PATH="$PATH:/path/to/crm"' >> ~/.bashrc
```

Requires Python 3.6+.

## Usage

```
crm list [STAGE]           List contacts by stage
crm due [DAYS]             What needs action (default: 7 days)
crm show [QUERY]           Contact details + history
crm note [QUERY] [TEXT]    Add a timestamped note
crm stage [QUERY] [STAGE]  Move to new stage
crm next [QUERY] [ACTION] [DATE]   Set next action
crm edit [QUERY]           Edit contact (interactive form)
crm add contact            Add new contact (interactive form)
crm add stage [NAME]       Add a stage
crm add source [NAME]      Add a source
crm rm contact [QUERY]     Remove contact (soft delete)
crm rm stage [NAME]        Remove a stage (if empty)
crm rm source [NAME]       Remove a source (if unused)
crm restore [QUERY]        Restore a removed contact
crm search TERM            Search across everything
crm stages                 List stages
crm config [KEY] [VALUE]   Get/set config (e.g., timezone)
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
cold → contacted → responded → meeting → proposal → won
                                                  ↘ lost
                                                  ↘ dormant
```

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

## Data

Everything lives in `crm_data.json` next to the script. Back it up however you like — it's just a file.

```bash
# Backup
cp crm_data.json ~/backup/

# Sync across machines
# Just sync the JSON file
```

## Why this exists

Most CRMs are overkill for a solo practice. You don't need dashboards, integrations, or a monthly fee. You need to know who to follow up with tomorrow.

## License

MIT
