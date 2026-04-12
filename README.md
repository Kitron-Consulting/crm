# crm

A lightweight CLI pipeline tracker for solo consultants and freelancers. No dependencies, no database, no bullshit.

Your contacts live in a JSON file. You manage them from the terminal.

## Install

```bash
# Clone and add to PATH
git clone https://github.com/kitron-dev/crm.git
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
crm edit [QUERY]           Open in $EDITOR
crm add                    Add new contact
crm rm [QUERY]             Remove contact
crm search TERM            Search across everything
crm stages                 List valid stages
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

```
cold → contacted → responded → meeting → proposal → won
                                                  ↘ lost
                                                  ↘ dormant
```

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
