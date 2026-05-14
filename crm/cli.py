#!/usr/bin/env python3
"""
crm — lightweight CLI pipeline tracker

USAGE
  crm [--data FILE] <command> [arguments]
  crm --version

COMMANDS
  list [STAGE]           List all contacts grouped by stage
                         Optional: filter by stage (e.g., crm list meeting)

  due [DAYS]             Show what needs action (default: next 7 days)
  
  show [QUERY]           Show contact details + full history
                         Without QUERY: interactive picker
  
  note [QUERY] [TEXT]    Add a timestamped note
                         Without args: interactive

  notes [QUERY]          View, edit, delete notes for a contact

  stage [QUERY] [STAGE]  Move contact to new stage
                         Without args: interactive
  
  next [QUERY] [ACTION] [DATE]
                         Set next action and due date
                         DATE can be YYYY-MM-DD or relative like +7d

  done [QUERY]           Mark current action as completed

  followup [QUERY] [--template NAME] [--dry-run] [--to EMAIL] [--no-context]
                         Send a templated follow-up email
                         Shows recent IMAP exchange as context by default

  thread [QUERY]         Browse recent email thread with a contact

  templates              List email templates
  
  edit [QUERY] [--field value ...]
                         Edit contact (interactive form, or set fields directly)

  add contact [--name X --email X ...]
                         Add new contact (interactive form, or pass flags)
  add stage [NAME]       Add a stage
  add source [NAME]      Add a source
  add template [NAME]    Add/edit an email template

  rm contact [QUERY] [-y]  Remove a contact (soft delete, -y to skip confirm)
  rm stage [NAME]        Remove a stage (if empty)
  rm source [NAME]       Remove a source (if unused)
  rm template [NAME]     Remove an email template

  restore [QUERY]        Restore a removed contact

  search TERM            Search across all contacts and notes
  
  stages                 List stages

  config [KEY] [VALUE]   Get/set config (e.g., timezone)

  where                  Show where the data is stored (active backend)

  update [--check]       Self-update from the latest GitHub release

  help [COMMAND]         Show help for a command

EXAMPLES
  crm due                What's due this week?
  crm due 14             What's due in the next 2 weeks?
  crm list               Show full pipeline
  crm list meeting       Show only meetings
  crm show               Pick contact interactively
  crm show mekitec       Show Mekitec contact details
  crm note               Pick contact, then type note
  crm note mekitec "Called, no answer"
  crm stage              Pick contact and stage interactively
  crm stage mekitec responded
  crm next mekitec "Send proposal" +3d
  crm edit               Pick contact to edit
  crm add contact --name "John" --email "j@co.com" --company "Co"
  crm edit acme --stage meeting --role "CTO"
  crm rm contact acme -y

STAGES (default, configurable)
  cold · contacted · responded · meeting · proposal · won · lost · dormant

FILES
  Default: ~/.config/kitron-crm/crm_data.json
  Override:
    CRM_STORAGE=/path/to/file.json        local file at that path
    CRM_STORAGE=s3://bucket/key.json      S3-compatible object storage
    CRM_DATA=/path/to/file.json           legacy alias for the local path
    --data PATH                           per-invocation local override
  Run `crm where` to see the active backend.
"""

import json
import sys
import os
import re
import readline  # enables line editing (arrow keys, history) in input()
import curses
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path

from . import storage
from .stages import DEFAULT_STAGES, DEFAULT_SOURCES, get_stages, get_sources
from .storage import load_data, save_data, get_tz, CURRENT_VERSION, MIGRATIONS, ConcurrentWriteError, _parse_tz
from .due import parse_date, relative_date, bucket_due
from .notes import utc_stamp, add_note, edit_note, delete_note
from .contacts import find_contact, _contact_filter, search_contacts
from .display import (
    BOLD, DIM, RED, GREEN, YELLOW, BLUE, MAGENTA, CYAN, RESET,
    STAGE_COLOR_CYCLE, STAGE_COLOR_SPECIAL, stage_color,
    display_stamp,
    edit_text, prompt_input, prompt_confirm,
    pick_one, form_edit,
    format_contact_option, format_contact_line,
    pick_contact_from_all, pick_contact_from_matches, get_contact,
    _init_curses_colors,
)
# Mail symbols are lazy-imported inside the commands that use them
# (cmd_followup, cmd_thread, cmd_templates) — keeps smtplib/imaplib/
# email out of the import graph for every non-mail invocation.

def cmd_list(args):
    if len(args) > 1:
        print("Usage: crm list [STAGE]")
        return
    data = load_data()
    filter_stage = args[0].lower() if args else None
    
    contacts = data["contacts"]
    if filter_stage:
        contacts = [c for c in contacts if c["stage"].lower() == filter_stage]
    
    by_stage = {}
    for c in contacts:
        stage = c["stage"]
        if stage not in by_stage:
            by_stage[stage] = []
        by_stage[stage].append(c)
    
    stages = get_stages(data)
    tz = get_tz(data)
    today = datetime.now(tz).strftime("%Y-%m-%d")

    # Pipeline summary
    total = len(contacts)
    if total:
        parts = []
        for stage in stages:
            count = len(by_stage.get(stage, []))
            if count:
                sc = stage_color(stage, stages)
                parts.append(f"{sc}{stage} {BOLD}{count}{RESET}")
        print(f"\n{DIM}Pipeline:{RESET} {f' {DIM}·{RESET} '.join(parts)} {DIM}({total} total){RESET}")

    for stage in stages:
        if stage in by_stage:
            sc = stage_color(stage, stages)
            print(f"\n{sc}{BOLD}{stage.upper()}{RESET} ({len(by_stage[stage])})")
            for c in sorted(by_stage[stage], key=lambda x: x.get("next_date") or "z"):
                print(format_contact_line(c, stages, today))
    print()

def cmd_show(args):
    full = "--full" in args
    args = [a for a in args if a != "--full"]
    if len(args) > 1:
        print("Usage: crm show [QUERY] [--full]")
        return
    data = load_data()
    c = get_contact(data, args[0] if args else None, "Show which contact?")
    if not c:
        return
    
    stages = get_stages(data)
    sc = stage_color(c['stage'], stages)
    print(f"\n{DIM}{'='*50}{RESET}")
    print(f"{BOLD}{c['name']}{RESET} {DIM}—{RESET} {c['company']}")
    print(f"{DIM}{'='*50}{RESET}")
    print(f"  {DIM}Email:{RESET}   {CYAN}{c['email']}{RESET}")
    print(f"  {DIM}Phone:{RESET}   {CYAN}{c.get('phone', '')}{RESET}")
    print(f"  {DIM}Role:{RESET}    {c.get('role', '')}")
    print(f"  {DIM}Source:{RESET}  {BOLD}{c.get('source', '').upper()}{RESET}")
    print(f"  {DIM}Stage:{RESET}   {sc}{BOLD}{c['stage'].upper()}{RESET}")
    tz = get_tz(data)
    today = datetime.now(tz).strftime("%Y-%m-%d")
    next_date = c.get('next_date', '')
    next_action = c.get('next_action', '')
    if next_date and next_action:
        rel = relative_date(next_date, today)
        overdue = next_date < today
        dc = RED if overdue else GREEN
        print(f"  {DIM}Next:{RESET}    {dc}{rel}{RESET} {DIM}—{RESET} {next_action}")
    else:
        print(f"  {DIM}Next:{RESET}    {DIM}none{RESET}")
    notes = c.get("notes", [])
    try:
        term_w = os.get_terminal_size().columns
    except OSError:
        term_w = 80
    max_text = term_w - 24  # room for indent + date prefix

    print(f"\n  {BOLD}Notes ({len(notes)}):{RESET}")
    for note in notes:
        text = note['text']
        ds = display_stamp(note['date'], data)
        if full:
            print(f"    {DIM}[{ds}]{RESET} {text}")
        else:
            truncated = text[:max_text] + "..." if len(text) > max_text else text
            print(f"    {DIM}[{ds}]{RESET} {truncated}")
    print()

    if not full and any(len(n['text']) > max_text for n in notes):
        print(f"  {DIM}Use --full to see complete notes{RESET}\n")

def cmd_note(args):
    if len(args) > 2:
        print("Usage: crm note [QUERY] \"[TEXT]\"")
        print("  Multi-word text must be quoted: crm note acme \"Called, no answer\"")
        return
    data = load_data()

    if args:
        c = get_contact(data, args[0], "Add note to which contact?")
        note_text = args[1] if len(args) > 1 else None
    else:
        c = get_contact(data, None, "Add note to which contact?")
        note_text = None
    
    if not c:
        return

    if not note_text:
        note_text = edit_text(header=f"Add note to {c['name']}")
        if not note_text:
            print(f"  {DIM}Cancelled.{RESET}")
            return

    add_note(c, note_text)

    save_data(data)
    print(f"{GREEN}Added note to {BOLD}{c['name']}{RESET}")

def cmd_notes(args):
    if len(args) > 1:
        print("Usage: crm notes [QUERY]")
        return
    data = load_data()
    c = get_contact(data, args[0] if args else None, "View notes for which contact?")
    if not c:
        return

    notes = c.get("notes", [])
    if not notes:
        print(f"  {DIM}No notes for {c['name']}.{RESET}")
        return

    try:
        term_w = os.get_terminal_size().columns
    except OSError:
        term_w = 80
    max_text = term_w - 24

    # Show truncated notes
    print(f"\n{BOLD}{c['name']}{RESET} {DIM}—{RESET} {c['company']}")
    print(f"{DIM}{'='*50}{RESET}")
    for note in notes:
        first_line = note['text'].split('\n')[0]
        truncated = first_line[:max_text] + "..." if len(first_line) > max_text else first_line
        multiline = "\n" in note['text']
        suffix = f" {DIM}[+]{RESET}" if multiline else ""
        print(f"  {DIM}[{display_stamp(note['date'], data)}]{RESET} {truncated}{suffix}")
    print()

    if not sys.stdin.isatty():
        return

    action_result = [None]

    def notes_viewer(stdscr):
        _init_curses_colors()
        curses.curs_set(0)
        cursor = 0

        need_clear = False
        prev_size = None
        while True:
            try:
                ts = os.get_terminal_size()
                new_size = (ts.lines, ts.columns)
            except OSError:
                new_size = stdscr.getmaxyx()
            if new_size != prev_size:
                curses.resizeterm(*new_size)
                stdscr.clear()
                prev_size = new_size
            elif need_clear:
                stdscr.clear()
                need_clear = False
            else:
                stdscr.erase()
            h, w = stdscr.getmaxyx()

            # Header
            title = f"{c['name']} — {c['company']} ({len(notes)} notes)"
            stdscr.addnstr(0, 0, title, w - 1, curses.A_BOLD)

            # Split: top half = note list, bottom half = preview
            list_h = max(h // 3, 5)
            preview_start = list_h + 1

            # Note list
            for i in range(min(len(notes), list_h - 2)):
                note = notes[i]
                first_line = note['text'].split('\n')[0]
                date_prefix = f"[{display_stamp(note['date'], data)}] "
                avail = w - 6 - len(date_prefix)
                truncated = first_line[:avail] + "..." if len(first_line) > avail or '\n' in note['text'] else first_line
                if i == cursor:
                    try:
                        stdscr.addstr(i + 2, 2, "▸ ", curses.color_pair(1))
                        stdscr.addstr(i + 2, 4, date_prefix, curses.A_DIM)
                        stdscr.addnstr(i + 2, 4 + len(date_prefix), truncated, avail, curses.A_BOLD)
                    except curses.error:
                        pass
                else:
                    try:
                        stdscr.addstr(i + 2, 4, date_prefix, curses.A_DIM)
                        stdscr.addnstr(i + 2, 4 + len(date_prefix), truncated, avail)
                    except curses.error:
                        pass

            # Divider
            if preview_start < h:
                try:
                    stdscr.addnstr(preview_start - 1, 0, "─" * w, w, curses.A_DIM)
                except curses.error:
                    pass

            # Preview of selected note
            if cursor < len(notes):
                selected = notes[cursor]
                preview_h = h - preview_start - 2
                if preview_h > 0 and preview_start < h:
                    try:
                        stdscr.addstr(preview_start, 2, f"[{display_stamp(selected['date'], data)}]", curses.A_DIM)
                    except curses.error:
                        pass
                    # Word-wrap the note text, respecting newlines
                    wrap_w = w - 5
                    lines = []
                    for paragraph in selected['text'].splitlines():
                        if not paragraph:
                            lines.append("")
                            continue
                        while paragraph and len(lines) < preview_h:
                            if len(paragraph) <= wrap_w:
                                lines.append(paragraph)
                                break
                            cut = paragraph[:wrap_w].rfind(' ')
                            if cut <= 0:
                                cut = wrap_w
                            lines.append(paragraph[:cut])
                            paragraph = paragraph[cut:].lstrip()
                        if len(lines) >= preview_h:
                            break
                    for j, line in enumerate(lines):
                        if preview_start + 1 + j < h - 1:
                            try:
                                stdscr.addnstr(preview_start + 1 + j, 4, line, w - 5)
                            except curses.error:
                                pass

            # Footer
            footer = "↑↓ navigate · a add · e edit · d delete · esc back"
            try:
                stdscr.addnstr(h - 1, 2, footer, w - 3, curses.A_DIM)
            except curses.error:
                pass

            stdscr.refresh()
            try:
                key = stdscr.get_wch()
            except curses.error:
                need_clear = True
                continue

            if key == curses.KEY_RESIZE:
                need_clear = True
                continue
            elif key == "\x1b" or key == "\x03":
                return
            elif key == curses.KEY_UP:
                if cursor > 0:
                    cursor -= 1
            elif key == curses.KEY_DOWN:
                if cursor < len(notes) - 1:
                    cursor += 1
            elif key == "a":
                action_result[0] = ("add", None)
                return
            elif key == "e" and cursor < len(notes):
                action_result[0] = ("edit", notes[cursor])
                return
            elif key == "d" and cursor < len(notes):
                action_result[0] = ("delete", notes[cursor])
                return

    try:
        curses.wrapper(notes_viewer)
    except KeyboardInterrupt:
        pass

    if not action_result[0]:
        return

    action, note = action_result[0]

    if action == "add":
        text = edit_text(header=f"Add note to {c['name']}")
        if not text:
            return
        add_note(c, text)
        save_data(data)
        print(f"{GREEN}Added note to {BOLD}{c['name']}{RESET}")

    elif action == "edit":
        text = edit_text(initial=note["text"], header=f"Edit note from {display_stamp(note['date'], data)}")
        if not text:
            return
        edit_note(note, text)
        save_data(data)
        print(f"{GREEN}Updated note{RESET}")

    elif action == "delete":
        if prompt_confirm(f"Delete note from {display_stamp(note['date'], data)}?"):
            delete_note(notes, note)
            save_data(data)
            print(f"{YELLOW}Deleted note{RESET}")

def cmd_followup(args):
    from .mail import (
        get_templates, get_smtp_config, contact_context, render_template,
        build_message, send_email, save_to_sent, fetch_thread,
    )
    # Parse flags
    dry_run = "--dry-run" in args
    no_context = "--no-context" in args
    args = [a for a in args if a != "--dry-run" and a != "--no-context"]
    template_name = None
    override_to = None
    query = None
    i = 0
    while i < len(args):
        if args[i] == "--template" and i + 1 < len(args):
            template_name = args[i + 1]
            i += 2
        elif args[i] == "--to" and i + 1 < len(args):
            override_to = args[i + 1]
            i += 2
        elif query is None:
            query = args[i]
            i += 1
        else:
            print(f"Unknown arg: {args[i]}")
            return

    data = load_data()
    templates = get_templates(data)
    if not templates:
        print(f"{RED}No templates configured.{RESET} Add one with: crm add template <name>")
        return

    c = get_contact(data, query, "Send follow-up to which contact?")
    if not c:
        return

    if not c.get("email"):
        print(f"{RED}{c['name']} has no email address.{RESET}")
        return

    # Fetch email context if IMAP configured
    imap_cfg = data.get("config", {}).get("imap")
    context_lines = []
    recent_messages = []
    if imap_cfg and not no_context and sys.stdin.isatty():
        print(f"{DIM}Fetching recent messages...{RESET}")
        try:
            messages = fetch_thread(imap_cfg, c["email"])
        except Exception as e:
            print(f"{YELLOW}Warning: couldn't fetch thread: {e}{RESET}")
            messages = []

        if messages:
            last_in = next((m for m in messages if m["direction"] == "inbound"), None)
            last_out = next((m for m in messages if m["direction"] == "outbound"), None)
            recent_messages = [m for m in [last_in, last_out] if m]

            for m in recent_messages:
                arrow = "from" if m["direction"] == "inbound" else "to"
                snippet = m['body'].strip().split('\n')[0][:100]
                context_lines.append(f"[{m['date']}] {arrow} {c['email']}: {m['subject']}")
                if snippet:
                    context_lines.append(f"    {snippet}")

            # Warn if they replied since your last send — ask before going further
            if last_in and last_out and last_in["dt"] and last_out["dt"]:
                if last_in["dt"] > last_out["dt"]:
                    print(f"\n  {YELLOW}{BOLD}⚠ They replied after your last message.{RESET}")
                    arrow_in = "from"
                    print(f"  {DIM}[{last_in['date']}]{RESET} {CYAN}←{RESET} {BOLD}{last_in['subject']}{RESET}")
                    snip = last_in['body'].strip().split('\n')[0][:120]
                    if snip:
                        print(f"      {DIM}{snip}{RESET}")
                    if not prompt_confirm("Continue with followup?"):
                        print(f"  {DIM}Cancelled.{RESET}")
                        return

    # Pick template
    if template_name:
        if template_name not in templates:
            print(f"{RED}Template '{template_name}' not found.{RESET} Available: {', '.join(templates)}")
            return
    else:
        template_name = pick_one(sorted(templates.keys()), prompt="Select template")
        if not template_name:
            return

    tmpl = templates[template_name]
    ctx = contact_context(c)
    subject = render_template(tmpl.get("subject", ""), ctx)
    body = render_template(tmpl.get("body", ""), ctx)

    to_addr = override_to or c["email"]

    # Offer to view recent messages in pager before composing
    if recent_messages and sys.stdin.isatty():
        if prompt_confirm("View recent messages before composing?", default=False):
            content = ""
            for m in recent_messages:
                arrow = "← from" if m["direction"] == "inbound" else "→ to"
                addr = m['from'] if m['direction'] == 'inbound' else m['to']
                content += (
                    f"Date:    {m['date']}\n"
                    f"{arrow}: {addr}\n"
                    f"Subject: {m['subject'] or '(no subject)'}\n"
                    f"{'-' * 60}\n\n"
                    f"{m['body']}\n\n"
                    f"{'=' * 60}\n\n"
                )
            pager = os.environ.get("PAGER", "less")
            try:
                subprocess.run([pager], input=content, text=True)
            except FileNotFoundError:
                print(content)
                input("Press enter to continue...")

    # Open editor with filled template
    initial = f"Subject: {subject}\n\n{body}"
    header = [
        f"To: {to_addr}",
        "Format: first line 'Subject: <subject>', blank line, then the body.",
        "You'll be asked to confirm before sending.",
    ]
    if context_lines:
        header.append("")
        header.append("Recent exchange:")
        header.extend(context_lines)
    edited = edit_text(initial=initial, header=header)
    if not edited:
        print(f"  {DIM}Cancelled.{RESET}")
        return

    # Parse subject + body back
    lines = edited.splitlines()
    if not lines or not lines[0].startswith("Subject: "):
        print(f"{RED}First line must be: Subject: <subject>{RESET}")
        return
    subject = lines[0][len("Subject: "):].strip()
    # Skip blank lines after subject
    idx = 1
    while idx < len(lines) and not lines[idx].strip():
        idx += 1
    body = "\n".join(lines[idx:])
    if not subject or not body.strip():
        print(f"{RED}Subject and body required.{RESET}")
        return

    # Show summary
    print(f"\n{DIM}{'='*50}{RESET}")
    print(f"{DIM}To:{RESET}      {to_addr}")
    print(f"{DIM}Subject:{RESET} {BOLD}{subject}{RESET}")
    print(f"{DIM}{'-'*50}{RESET}")
    print(body)
    print(f"{DIM}{'='*50}{RESET}")

    if dry_run:
        print(f"\n{YELLOW}Dry run — email not sent.{RESET}")
        return

    smtp_cfg = get_smtp_config(data)
    if not smtp_cfg:
        print(f"{RED}SMTP not configured. Set it in config.{RESET}")
        return

    if not prompt_confirm(f"Send this email to {to_addr}?", default=True):
        print(f"  {DIM}Cancelled.{RESET}")
        return

    msg = build_message(smtp_cfg, to_addr, subject, body)

    try:
        send_email(smtp_cfg, msg)
    except Exception as e:
        print(f"{RED}Send failed: {e}{RESET}")
        return

    # Save to IMAP Sent folder if configured
    imap_cfg = data.get("config", {}).get("imap")
    if imap_cfg:
        try:
            save_to_sent(imap_cfg, msg)
        except Exception as e:
            print(f"{YELLOW}Warning: sent, but couldn't save to IMAP Sent folder: {e}{RESET}")

    # Log as note
    stamp = utc_stamp()
    if "notes" not in c:
        c["notes"] = []
    note_text = f"Sent email: {subject}"
    if override_to:
        note_text += f" (to {override_to})"
    c["notes"].insert(0, {"date": stamp, "text": note_text})

    # Optionally set next action
    if prompt_confirm("Set follow-up reminder?", default=True):
        tz = get_tz(data)
        date = (datetime.now(tz) + timedelta(days=7)).strftime("%Y-%m-%d")
        c["next_action"] = "Wait for response"
        c["next_date"] = date
        print(f"{DIM}Set next: {c['next_action']} on {date}{RESET}")

    save_data(data)
    print(f"{GREEN}Sent to {BOLD}{to_addr}{RESET}")

def cmd_add_template(args):
    if not args:
        print("Usage: crm add template <name>")
        return
    name = args[0]
    data = load_data()
    if "templates" not in data["config"]:
        data["config"]["templates"] = {}
    templates = data["config"]["templates"]
    existing = templates.get(name, {"subject": "", "body": ""})

    initial = f"Subject: {existing['subject']}\n\n{existing['body']}"
    header = [
        f"Template: {name}",
        "Format: first line 'Subject: <subject>', blank line, then the body.",
        "Placeholders: {name} {first_name} {company} {role} {email} {phone}",
    ]
    edited = edit_text(initial=initial, header=header)
    if not edited:
        print(f"  {DIM}Cancelled.{RESET}")
        return

    lines = edited.splitlines()
    if not lines or not lines[0].startswith("Subject: "):
        print(f"{RED}First line must be: Subject: <subject>{RESET}")
        return
    subject = lines[0][len("Subject: "):].strip()
    idx = 1
    while idx < len(lines) and not lines[idx].strip():
        idx += 1
    body = "\n".join(lines[idx:])

    templates[name] = {"subject": subject, "body": body}
    save_data(data)
    print(f"{GREEN}Saved template: {BOLD}{name}{RESET}")

def cmd_rm_template(args):
    if not args:
        print("Usage: crm rm template <name>")
        return
    name = args[0]
    data = load_data()
    templates = data.get("config", {}).get("templates", {})
    if name not in templates:
        print(f"Template '{name}' not found.")
        return
    del templates[name]
    save_data(data)
    print(f"{YELLOW}Removed template: {BOLD}{name}{RESET}")

def cmd_templates(args):
    from .mail import get_templates
    data = load_data()
    templates = get_templates(data)
    if not templates:
        print(f"  {DIM}No templates. Add one with: crm add template <name>{RESET}")
        return
    print("Templates:")
    for name, t in sorted(templates.items()):
        print(f"  {BOLD}{name}{RESET} {DIM}—{RESET} {t.get('subject', '')}")

def cmd_thread(args):
    from .mail import fetch_thread
    if len(args) > 1:
        print("Usage: crm thread [QUERY]")
        return
    data = load_data()
    imap_cfg = data.get("config", {}).get("imap")
    if not imap_cfg:
        print(f"{RED}IMAP not configured. Add config.imap to crm_data.json.{RESET}")
        return

    c = get_contact(data, args[0] if args else None, "Show thread for which contact?")
    if not c:
        return
    if not c.get("email"):
        print(f"{RED}{c['name']} has no email address.{RESET}")
        return

    print(f"{DIM}Fetching messages for {c['email']}...{RESET}")
    try:
        messages = fetch_thread(imap_cfg, c["email"])
    except Exception as e:
        print(f"{RED}IMAP fetch failed: {e}{RESET}")
        return

    if not messages:
        print(f"  {DIM}No messages found for {c['email']}.{RESET}")
        return

    # Non-interactive: print summary
    if not sys.stdin.isatty():
        print(f"\n{BOLD}Thread: {c['name']} ({c['email']}){RESET}")
        for m in messages:
            arrow = "←" if m["direction"] == "inbound" else "→"
            dc = CYAN if m["direction"] == "inbound" else GREEN
            print(f"  {DIM}[{m['date']}]{RESET} {dc}{arrow}{RESET} {BOLD}{m['subject']}{RESET}")
        return

    # Interactive curses viewer (reuse notes viewer pattern)
    cursor = 0

    def thread_viewer(stdscr):
        _init_curses_colors()
        curses.curs_set(0)
        nonlocal cursor

        need_clear = False
        prev_size = None
        while True:
            try:
                ts = os.get_terminal_size()
                new_size = (ts.lines, ts.columns)
            except OSError:
                new_size = stdscr.getmaxyx()
            if new_size != prev_size:
                curses.resizeterm(*new_size)
                stdscr.clear()
                prev_size = new_size
            elif need_clear:
                stdscr.clear()
                need_clear = False
            else:
                stdscr.erase()
            h, w = stdscr.getmaxyx()

            # Header
            title = f"Thread: {c['name']} — {c['email']} ({len(messages)} messages)"
            stdscr.addnstr(0, 0, title, w - 1, curses.A_BOLD)

            # Split: top = list, bottom = preview
            list_h = max(h // 3, 5)
            preview_start = list_h + 1

            for i in range(min(len(messages), list_h - 2)):
                m = messages[i]
                arrow = "← " if m["direction"] == "inbound" else "→ "
                color = curses.color_pair(3) if m["direction"] == "inbound" else curses.color_pair(1)
                date_prefix = f"[{m['date']}] "
                subj = m['subject'] or "(no subject)"
                row_i = i + 2
                try:
                    if i == cursor:
                        stdscr.addstr(row_i, 2, "▸ ", curses.color_pair(1))
                    stdscr.addstr(row_i, 4, date_prefix, curses.A_DIM)
                    stdscr.addstr(row_i, 4 + len(date_prefix), arrow, color)
                    avail = w - 6 - len(date_prefix) - len(arrow)
                    attr = curses.A_BOLD if i == cursor else 0
                    stdscr.addnstr(row_i, 4 + len(date_prefix) + len(arrow), subj[:avail], avail, attr)
                except curses.error:
                    pass

            # Divider
            if preview_start < h:
                try:
                    stdscr.addnstr(preview_start - 1, 0, "─" * w, w, curses.A_DIM)
                except curses.error:
                    pass

            # Preview
            if cursor < len(messages):
                m = messages[cursor]
                preview_h = h - preview_start - 2
                if preview_h > 0 and preview_start < h:
                    header_line = f"[{m['date']}] {'← from' if m['direction'] == 'inbound' else '→ to'} {m['from'] if m['direction']=='inbound' else m['to']}"
                    try:
                        stdscr.addnstr(preview_start, 2, header_line, w - 3, curses.A_DIM)
                        subj_line = f"Subject: {m['subject'] or '(no subject)'}"
                        stdscr.addnstr(preview_start + 1, 2, subj_line, w - 3, curses.A_BOLD)
                    except curses.error:
                        pass
                    # Word-wrap body
                    wrap_w = w - 5
                    lines_out = []
                    for paragraph in m['body'].splitlines():
                        if not paragraph.strip():
                            lines_out.append("")
                            continue
                        while paragraph and len(lines_out) < preview_h - 2:
                            if len(paragraph) <= wrap_w:
                                lines_out.append(paragraph)
                                break
                            cut = paragraph[:wrap_w].rfind(' ')
                            if cut <= 0:
                                cut = wrap_w
                            lines_out.append(paragraph[:cut])
                            paragraph = paragraph[cut:].lstrip()
                        if len(lines_out) >= preview_h - 2:
                            break
                    for j, line in enumerate(lines_out):
                        if preview_start + 3 + j < h - 1:
                            try:
                                stdscr.addnstr(preview_start + 3 + j, 4, line, w - 5)
                            except curses.error:
                                pass

            footer = "↑↓ navigate · enter open full · esc back"
            try:
                stdscr.addnstr(h - 1, 2, footer, w - 3, curses.A_DIM)
            except curses.error:
                pass

            stdscr.refresh()
            try:
                key = stdscr.get_wch()
            except curses.error:
                need_clear = True
                continue

            if key == curses.KEY_RESIZE:
                need_clear = True
                continue
            elif key == "\x1b" or key == "\x03":
                return
            elif key == curses.KEY_UP:
                if cursor > 0:
                    cursor -= 1
            elif key == curses.KEY_DOWN:
                if cursor < len(messages) - 1:
                    cursor += 1
            elif key == "\n" or key == "\r" or key == curses.KEY_ENTER or key == "o":
                if cursor < len(messages):
                    m = messages[cursor]
                    view_message_in_pager(m)
                    need_clear = True

    try:
        curses.wrapper(thread_viewer)
    except KeyboardInterrupt:
        pass

def view_message_in_pager(m):
    """Display a message in $PAGER (or less)."""
    arrow = "← from" if m["direction"] == "inbound" else "→ to"
    addr = m['from'] if m['direction'] == 'inbound' else m['to']
    content = (
        f"Date:    {m['date']}\n"
        f"{arrow}: {addr}\n"
        f"Subject: {m['subject'] or '(no subject)'}\n"
        f"{'-' * 60}\n\n"
        f"{m['body']}\n"
    )
    pager = os.environ.get("PAGER", "less")
    try:
        try:
            curses.endwin()
        except curses.error:
            pass
        subprocess.run([pager], input=content, text=True)
    except FileNotFoundError:
        # Fallback: just print it
        print(content)
        input("Press enter to continue...")

def cmd_done(args):
    if len(args) > 1:
        print("Usage: crm done [QUERY]")
        return
    data = load_data()
    c = get_contact(data, args[0] if args else None, "Mark done for which contact?")
    if not c:
        return

    action = c.get("next_action", "")
    date = c.get("next_date", "")
    if not action:
        print(f"  {DIM}No action set for {c['name']}.{RESET}")
        return

    stamp = utc_stamp()
    if "notes" not in c:
        c["notes"] = []
    c["notes"].insert(0, {"date": stamp, "text": f"Done: {action}"})
    c["next_action"] = ""
    c["next_date"] = ""

    save_data(data)
    print(f"{GREEN}Completed: {BOLD}{action}{RESET} {DIM}({c['name']}){RESET}")

def cmd_stage(args):
    if len(args) > 2:
        print("Usage: crm stage [QUERY] [STAGE]")
        return
    data = load_data()

    if args:
        c = get_contact(data, args[0], "Change stage for which contact?")
        new_stage = args[1].lower() if len(args) > 1 else None
    else:
        c = get_contact(data, None, "Change stage for which contact?")
        new_stage = None
    
    if not c:
        return
    
    stages = get_stages(data)
    if not new_stage:
        new_stage = pick_one(stages, prompt=f"New stage for {c['name']} (current: {c['stage']})")
        if not new_stage:
            return

    if new_stage not in stages:
        print(f"Invalid stage. Use: {', '.join(stages)}")
        return
    
    old_stage = c["stage"]
    c["stage"] = new_stage
    
    stamp = utc_stamp()
    if "notes" not in c:
        c["notes"] = []
    c["notes"].insert(0, {"date": stamp, "text": f"Stage: {old_stage} → {new_stage}"})
    
    save_data(data)
    print(f"{BOLD}{c['name']}{RESET}: {DIM}{old_stage}{RESET} → {GREEN}{new_stage}{RESET}")

def cmd_next(args):
    if len(args) > 3:
        print("Usage: crm next [QUERY] \"[ACTION]\" [DATE]")
        print("  Multi-word actions must be quoted: crm next acme \"Send proposal\" +3d")
        return
    data = load_data()

    if args:
        c = get_contact(data, args[0], "Set next action for which contact?")
        action = args[1] if len(args) > 1 else None
        date = args[2] if len(args) > 2 else None
    else:
        c = get_contact(data, None, "Set next action for which contact?")
        action = None
        date = None
    
    if not c:
        return
    
    if not action or not date:
        def validate_date(v):
            if v.startswith("+") and v.endswith("d"):
                return None
            try:
                datetime.strptime(v, "%Y-%m-%d")
            except ValueError:
                return "Use YYYY-MM-DD or +Nd"
            return None

        result = form_edit([
            {"name": "Action", "value": action or "", "required": True},
            {"name": "Due date", "value": date or "+7d", "required": True, "validate": validate_date},
        ], title=f"Next action for {c['name']}")
        if not result:
            print(f"  {DIM}Cancelled.{RESET}")
            return
        action = result["Action"]
        date = result["Due date"]

    parsed = parse_date(date, get_tz(data))
    if parsed is None:
        return

    c["next_action"] = action
    c["next_date"] = parsed
    
    save_data(data)
    print(f"{BOLD}{c['name']}{RESET}: next → {c['next_action']} {DIM}({RESET}{GREEN}{c['next_date']}{RESET}{DIM}){RESET}")

def cmd_due(args):
    if len(args) > 1:
        print("Usage: crm due [DAYS]")
        return
    data = load_data()
    tz = get_tz(data)
    days = int(args[0]) if args else 7
    cutoff = (datetime.now(tz) + timedelta(days=days)).strftime("%Y-%m-%d")
    today = datetime.now(tz).strftime("%Y-%m-%d")
    overdue, due = bucket_due(data["contacts"], today, cutoff)

    stages = get_stages(data)
    if overdue:
        print(f"\n{RED}{BOLD}OVERDUE ({len(overdue)}){RESET}")
        for c in sorted(overdue, key=lambda x: x["next_date"]):
            print(format_contact_line(c, stages, today))

    if due:
        print(f"\n{YELLOW}{BOLD}DUE WITHIN {days} DAYS ({len(due)}){RESET}")
        for c in sorted(due, key=lambda x: x["next_date"]):
            print(format_contact_line(c, stages, today))
    
    if not due and not overdue:
        print(f"\n{GREEN}Nothing due within {days} days.{RESET}")
        print()
        return

    # Non-interactive: just print the list
    if not sys.stdin.isatty():
        print()
        return

    # Interactive: picker with action flow
    all_due = sorted(overdue, key=lambda x: x["next_date"]) + sorted(due, key=lambda x: x["next_date"])
    def fmt(c):
        rel = relative_date(c.get("next_date", ""), today)
        act = c.get("next_action", "")
        base = format_contact_option(c, stages)
        return f"{base} {DIM}→{RESET} {rel}: {act}"
    c = pick_one(all_due, prompt=f"Due / overdue ({len(all_due)}) — select to take action", format_fn=fmt, filter_fn=_contact_filter)
    if not c:
        return

    actions = ["done", "followup", "note", "stage", "next", "show", "edit"]
    action = pick_one(actions, prompt=f"Action for {c['name']} ({c['company']})")
    if not action:
        return

    if action == "done":
        cmd_done([c["name"]])
    elif action == "followup":
        cmd_followup([c["name"]])
    elif action == "note":
        cmd_note([c["name"]])
    elif action == "stage":
        cmd_stage([c["name"]])
    elif action == "next":
        cmd_next([c["name"]])
    elif action == "show":
        cmd_show([c["name"]])
    elif action == "edit":
        cmd_edit([c["name"]])

def cmd_add(args):
    if not args:
        print("Usage: crm add <contact|stage|source|template>")
        return
    if args[0] == "stage":
        return cmd_add_stage(args[1:])
    if args[0] == "source":
        return cmd_add_source(args[1:])
    if args[0] == "template":
        return cmd_add_template(args[1:])
    if args[0] != "contact":
        print(f"Unknown: crm add {args[0]}. Use: crm add <contact|stage|source|template>")
        return

    # Parse --flags for non-interactive use
    rest = args[1:]
    flags = {}
    i = 0
    while i < len(rest):
        if rest[i].startswith("--") and i + 1 < len(rest):
            flags[rest[i][2:].lower()] = rest[i + 1]
            i += 2
        else:
            i += 1

    data = load_data()
    stages = get_stages(data)
    sources = get_sources(data)

    def validate_email(v):
        if v and "@" not in v:
            return "Must contain @"
        return None

    if "name" in flags:
        # Non-interactive mode
        email = flags.get("email", "")
        if validate_email(email):
            print(f"{RED}Invalid email: {email}{RESET}")
            return
        source = flags.get("source", "cold")
        if source not in sources:
            print(f"{RED}Invalid source: {source}. Use: {', '.join(sources)}{RESET}")
            return
        stage = flags.get("stage", "cold")
        if stage not in stages:
            print(f"{RED}Invalid stage: {stage}. Use: {', '.join(stages)}{RESET}")
            return
        result = {
            "Name": flags["name"],
            "Email": email,
            "Phone": flags.get("phone", ""),
            "Company": flags.get("company", ""),
            "Role": flags.get("role", ""),
            "Source": source,
            "Stage": stage,
        }
    else:
        # Interactive form
        form_fields = [
            {"name": "Name", "value": "", "required": True},
            {"name": "Email", "value": "", "validate": validate_email},
            {"name": "Phone", "value": ""},
            {"name": "Company", "value": ""},
            {"name": "Role", "value": ""},
            {"name": "Source", "value": "cold", "options": sources},
            {"name": "Stage", "value": "cold", "options": stages},
        ]
        result = form_edit(form_fields, title="Add new contact")
        if not result:
            print(f"  {DIM}Cancelled.{RESET}")
            return

    # Check for duplicates
    dupes = [c for c in data["contacts"] if c["name"].lower() == result["Name"].lower()]
    if dupes:
        print(f"  {YELLOW}Warning: '{dupes[0]['name']}' ({dupes[0].get('company', '')}) already exists.{RESET}")
        if not prompt_confirm("Continue anyway?"):
            print(f"  {DIM}Cancelled.{RESET}")
            return

    stamp = utc_stamp()

    contact = {
        "name": result["Name"],
        "email": result["Email"],
        "phone": result["Phone"],
        "company": result["Company"],
        "role": result["Role"],
        "source": result["Source"],
        "stage": result["Stage"],
        "next_action": "",
        "next_date": "",
        "notes": [{"date": stamp, "text": "Added to CRM"}]
    }

    data["contacts"].append(contact)
    save_data(data)
    print(f"{GREEN}Added {BOLD}{contact['name']}{RESET}")

def cmd_edit(args):
    # Parse query and --flags
    query = None
    flags = {}
    i = 0
    while i < len(args):
        if args[i].startswith("--") and i + 1 < len(args):
            flags[args[i][2:].lower()] = args[i + 1]
            i += 2
        elif query is None:
            query = args[i]
            i += 1
        else:
            i += 1

    data = load_data()
    c = get_contact(data, query, "Edit which contact?")
    if not c:
        return

    stages = get_stages(data)
    sources = get_sources(data)

    def validate_email(v):
        if v and "@" not in v:
            return "Must contain @"
        return None

    if flags:
        # Non-interactive mode — apply flags directly
        field_map = {"name": "name", "email": "email", "phone": "phone",
                     "company": "company", "role": "role", "source": "source", "stage": "stage"}
        for key, val in flags.items():
            if key not in field_map:
                print(f"{RED}Unknown field: {key}{RESET}")
                return
            if key == "email" and validate_email(val):
                print(f"{RED}Invalid email: {val}{RESET}")
                return
            if key == "source" and val not in sources:
                print(f"{RED}Invalid source: {val}. Use: {', '.join(sources)}{RESET}")
                return
            if key == "stage" and val not in stages:
                print(f"{RED}Invalid stage: {val}. Use: {', '.join(stages)}{RESET}")
                return
            c[field_map[key]] = val
    else:
        # Interactive form
        form_fields = [
            {"name": "Name", "value": c.get("name", ""), "required": True},
            {"name": "Email", "value": c.get("email", ""), "validate": validate_email},
            {"name": "Phone", "value": c.get("phone", "")},
            {"name": "Company", "value": c.get("company", "")},
            {"name": "Role", "value": c.get("role", "")},
            {"name": "Source", "value": c.get("source", "cold"), "options": sources},
            {"name": "Stage", "value": c.get("stage", "cold"), "options": stages},
        ]
        result = form_edit(form_fields, title=f"Edit {c['name']}")
        if not result:
            print(f"  {DIM}Cancelled.{RESET}")
            return
        c["name"] = result["Name"]
        c["email"] = result["Email"]
        c["phone"] = result["Phone"]
        c["company"] = result["Company"]
        c["role"] = result["Role"]
        c["source"] = result["Source"]
        c["stage"] = result["Stage"]

    save_data(data)
    print(f"{GREEN}Updated {BOLD}{c['name']}{RESET}")

def cmd_rm(args):
    if not args:
        print("Usage: crm rm <contact|stage|source|template>")
        return
    if args[0] == "stage":
        return cmd_rm_stage(args[1:])
    if args[0] == "source":
        return cmd_rm_source(args[1:])
    if args[0] == "template":
        return cmd_rm_template(args[1:])
    if args[0] != "contact":
        print(f"Unknown: crm rm {args[0]}. Use: crm rm <contact|stage|source|template>")
        return

    rest = args[1:]
    force = "-y" in rest
    if force:
        rest = [a for a in rest if a != "-y"]

    data = load_data()
    c = get_contact(data, rest[0] if rest else None, "Remove which contact?")
    if not c:
        return

    if not force and not prompt_confirm(f"Remove {BOLD}{c['name']}{RESET} ({c['company']})?"):
        print(f"  {DIM}Cancelled.{RESET}")
        return
    
    data["contacts"].remove(c)
    stamp = utc_stamp()
    c["removed_at"] = stamp
    data["removed"].append(c)
    save_data(data)
    print(f"{YELLOW}Removed {BOLD}{c['name']}{RESET}")

def cmd_restore(args):
    if len(args) > 1:
        print("Usage: crm restore [QUERY]")
        return
    data = load_data()
    removed = data.get("removed", [])
    if not removed:
        print("No removed contacts.")
        return

    if args:
        query = args[0].lower()
        matches = [c for c in removed if query in c["name"].lower() or query in c["company"].lower()]
    else:
        matches = removed

    if not matches:
        print(f"No removed contacts matching '{args[0]}'")
        return

    def format_removed(c):
        ra = display_stamp(c.get('removed_at', '?'), data)
        return f"{c['name']} ({c['company']}) [removed {ra}]"

    if len(matches) == 1:
        c = matches[0]
    else:
        c = pick_one(matches, prompt=f"Restore which contact? ({len(matches)} removed)", format_fn=format_removed)

    if not c:
        return

    dupes = [x for x in data["contacts"] if x["name"].lower() == c["name"].lower() and x["company"].lower() == c["company"].lower()]
    if dupes:
        print(f"{c['name']} ({c['company']}) already exists in contacts.")
        return

    data["removed"].remove(c)
    del c["removed_at"]
    data["contacts"].append(c)
    save_data(data)
    print(f"{GREEN}Restored {BOLD}{c['name']}{RESET} {DIM}({RESET}{c['company']}{DIM}){RESET}")

def cmd_config(args):
    data = load_data()

    cfg = data["config"]

    if not args:
        if not cfg:
            print("No config set.")
            return
        for k, v in cfg.items():
            print(f"  {k} = {v}")
        return

    key = args[0]

    if len(args) == 1:
        if key in cfg:
            print(f"  {key} = {cfg[key]}")
        else:
            print(f"  {key} not set")
        return

    # Set
    value = " ".join(args[1:])

    if key == "timezone":
        try:
            _parse_tz(value)
        except (ValueError, IndexError):
            print(f"Invalid timezone: {value}. Use format like UTC+03:00")
            return

    cfg[key] = value
    data["config"] = cfg
    save_data(data)
    print(f"  {key} = {value}")

def cmd_stages(args):
    data = load_data()
    stages = get_stages(data)
    print("Stages:")
    for s in stages:
        print(f"  {s}")

def cmd_add_stage(args):
    if not args:
        print("Usage: crm add stage <name>")
        return
    data = load_data()
    stages = get_stages(data)
    name = args[0].lower()
    if name in stages:
        print(f"Stage '{name}' already exists.")
        return
    stages.append(name)
    save_data(data)
    print(f"{GREEN}Added stage: {BOLD}{name}{RESET}")

def cmd_rm_stage(args):
    if not args:
        print("Usage: crm rm stage <name>")
        return
    data = load_data()
    stages = get_stages(data)
    name = args[0].lower()
    if name not in stages:
        print(f"Stage '{name}' not found.")
        return
    in_use = [c for c in data["contacts"] if c["stage"] == name]
    if in_use:
        print(f"Can't remove '{name}' — {len(in_use)} contact(s) in this stage.")
        return
    stages.remove(name)
    save_data(data)
    print(f"{YELLOW}Removed stage: {BOLD}{name}{RESET}")

def cmd_add_source(args):
    if not args:
        print("Usage: crm add source <name>")
        return
    data = load_data()
    sources = get_sources(data)
    name = args[0].lower()
    if name in sources:
        print(f"Source '{name}' already exists.")
        return
    sources.append(name)
    save_data(data)
    print(f"{GREEN}Added source: {BOLD}{name}{RESET}")

def cmd_rm_source(args):
    if not args:
        print("Usage: crm rm source <name>")
        return
    data = load_data()
    sources = get_sources(data)
    name = args[0].lower()
    if name not in sources:
        print(f"Source '{name}' not found.")
        return
    in_use = [c for c in data["contacts"] if c.get("source") == name]
    if in_use:
        print(f"Can't remove '{name}' — {len(in_use)} contact(s) with this source.")
        return
    sources.remove(name)
    save_data(data)
    print(f"{YELLOW}Removed source: {BOLD}{name}{RESET}")

def cmd_search(args):
    if not args:
        term = prompt_input("Search", required=True)
        if not term:
            return
    else:
        term = " ".join(args)

    data = load_data()
    results = search_contacts(
        data["contacts"],
        term,
        stamp_fmt=lambda d: display_stamp(d, data),
    )

    if not results:
        print(f"No results for '{term.lower()}'")
        return

    stages = get_stages(data)
    print(f"\n{BOLD}Found {len(results)} contact(s):{RESET}\n")
    for c, matches in results:
        sc = stage_color(c['stage'], stages)
        print(f"{BOLD}{c['name']}{RESET} {DIM}({RESET}{c['company']}{DIM}){RESET} {sc}[{c['stage'].upper()}]{RESET}")
        for m in matches[:3]:
            print(f"    {DIM}→{RESET} {m}")
        if len(matches) > 3:
            print(f"    {DIM}→ ...and {len(matches) - 3} more{RESET}")
        print()

HELP = {
    "list":    "crm list [STAGE]\n  List contacts grouped by stage. Optionally filter by stage name.",
    "due":     "crm due [DAYS]\n  Show overdue and upcoming actions. Default: 7 days.",
    "show":    "crm show [QUERY]\n  Show contact details and note history. Interactive picker if no query.",
    "note":    "crm note [QUERY] [TEXT]\n  Add a timestamped note to a contact. Interactive if args omitted.",
    "notes":   "crm notes [QUERY]\n  View all notes for a contact. Interactive: add, edit, or delete notes.",
    "stage":   "crm stage [QUERY] [STAGE]\n  Move a contact to a new pipeline stage. Interactive if args omitted.",
    "next":    "crm next [QUERY] [ACTION] [DATE]\n  Set next action and due date. DATE: YYYY-MM-DD or +Nd (e.g. +7d).",
    "done":    "crm done [QUERY]\n  Mark current action as completed. Logs it as a note and clears the action.",
    "followup": "crm followup [QUERY] [--template NAME] [--dry-run] [--to EMAIL] [--no-context]\n  Send a templated email. Opens $EDITOR to review before sending.\n  Templates support: {name} {first_name} {company} {role} {email} {phone}\n  If IMAP is configured, shows recent exchange as context (disable with --no-context).\n  Warns if the contact replied after your last message.\n  --dry-run shows the email without sending.\n  --to overrides the recipient (for testing).",
    "thread":   "crm thread [QUERY]\n  Browse recent email thread with a contact via IMAP.\n  Curses viewer: list on top, full message preview below. Requires config.imap.",
    "templates": "crm templates\n  List configured email templates.",
    "edit":    "crm edit [QUERY]\n  Edit a contact using an interactive form.\n\ncrm edit <QUERY> --field value [--field value ...]\n  Edit specific fields non-interactively.\n  Fields: name, email, phone, company, role, source, stage",
    "add":     "crm add contact\n  Add a new contact interactively.\n\ncrm add contact --name X [--email X] [--phone X] [--company X] [--role X] [--source X] [--stage X]\n  Add a contact non-interactively.\n\ncrm add stage <name>\n  Add a new pipeline stage.\n\ncrm add source <name>\n  Add a new contact source.\n\ncrm add template <name>\n  Add/edit an email template in $EDITOR.",
    "rm":      "crm rm contact [QUERY] [-y]\n  Soft-delete a contact. Use -y to skip confirmation.\n\ncrm rm stage <name>\n  Remove a stage (must have no contacts in it).\n\ncrm rm source <name>\n  Remove a source (must have no contacts using it).\n\ncrm rm template <name>\n  Remove an email template.",
    "restore": "crm restore [QUERY]\n  Restore a previously removed contact.",
    "search":  "crm search <TERM>\n  Search across all contact fields and notes.",
    "stages":  "crm stages\n  List all pipeline stages.",
    "config":  "crm config\n  Show all config.\n\ncrm config <key>\n  Get a config value.\n\ncrm config <key> <value>\n  Set a config value.\n\n  Available keys: timezone (e.g. UTC+03:00)",
}

def cmd_update(args):
    from .update import run
    run(args)

def cmd_where(args):
    backend = storage.current_backend()
    print(backend.describe())
    path = getattr(backend, "path", None)
    if path is not None:
        if path.exists():
            stat = path.stat()
            size_kb = stat.st_size / 1024
            mtime = datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M")
            print(f"  {size_kb:.1f} KB, modified {mtime}")
        else:
            print(f"  {DIM}(not created yet){RESET}")

def cmd_help(args):
    if not args:
        print(__doc__)
        return
    topic = args[0]
    if topic in HELP:
        print(f"\n{HELP[topic]}\n")
    else:
        print(f"No help for '{topic}'")

def cmd_dashboard(args):
    data = load_data()
    stages = get_stages(data)
    tz = get_tz(data)
    today = datetime.now(tz).strftime("%Y-%m-%d")
    contacts = data["contacts"]

    if not contacts:
        print(f"\n{DIM}No contacts yet. Run {RESET}{BOLD}crm add contact{RESET}{DIM} to get started.{RESET}\n")
        return

    # Pipeline summary
    by_stage = {}
    for c in contacts:
        s = c["stage"]
        if s not in by_stage:
            by_stage[s] = []
        by_stage[s].append(c)

    parts = []
    for stage in stages:
        count = len(by_stage.get(stage, []))
        if count:
            sc = stage_color(stage, stages)
            parts.append(f"{sc}{stage} {BOLD}{count}{RESET}")
    print(f"\n{BOLD}Pipeline{RESET} {DIM}({len(contacts)} contacts){RESET}")
    print(f"  {f' {DIM}·{RESET} '.join(parts)}")

    # Overdue
    overdue = [c for c in contacts if c.get("next_date") and c["next_date"] < today]
    if overdue:
        print(f"\n{RED}{BOLD}Overdue ({len(overdue)}){RESET}")
        for c in sorted(overdue, key=lambda x: x["next_date"])[:5]:
            days_over = (datetime.strptime(today, "%Y-%m-%d") - datetime.strptime(c["next_date"], "%Y-%m-%d")).days
            print(f"  {BOLD}{c['name']}{RESET} {DIM}({c['company']}){RESET} — {RED}{days_over}d overdue{RESET}: {c.get('next_action', '')}")
        if len(overdue) > 5:
            print(f"  {DIM}...and {len(overdue) - 5} more{RESET}")

    # Due this week
    week = (datetime.now(tz) + timedelta(days=7)).strftime("%Y-%m-%d")
    due_soon = [c for c in contacts if c.get("next_date") and today <= c["next_date"] <= week]
    if due_soon:
        print(f"\n{YELLOW}{BOLD}Due this week ({len(due_soon)}){RESET}")
        for c in sorted(due_soon, key=lambda x: x["next_date"])[:5]:
            days_until = (datetime.strptime(c["next_date"], "%Y-%m-%d") - datetime.strptime(today, "%Y-%m-%d")).days
            if days_until == 0:
                when = f"{YELLOW}today{RESET}"
            elif days_until == 1:
                when = f"{YELLOW}tomorrow{RESET}"
            else:
                when = f"in {days_until}d"
            print(f"  {BOLD}{c['name']}{RESET} {DIM}({c['company']}){RESET} — {when}: {c.get('next_action', '')}")
        if len(due_soon) > 5:
            print(f"  {DIM}...and {len(due_soon) - 5} more{RESET}")

    # No actions set
    no_action = [c for c in contacts if not c.get("next_date") and c["stage"] not in ("won", "lost", "dormant")]
    if no_action:
        print(f"\n{DIM}No next action ({len(no_action)}){RESET}")
        for c in no_action[:3]:
            sc = stage_color(c["stage"], stages)
            print(f"  {BOLD}{c['name']}{RESET} {DIM}({c['company']}){RESET} {sc}[{c['stage'].upper()}]{RESET}")
        if len(no_action) > 3:
            print(f"  {DIM}...and {len(no_action) - 3} more{RESET}")

    if not overdue and not due_soon:
        print(f"\n{GREEN}Nothing due this week.{RESET}")

    print(f"\n{DIM}Run {RESET}crm help{DIM} for commands.{RESET}")
    print()

def main():
    argv = sys.argv[1:]
    if argv and argv[0] in ("--version", "-V"):
        from . import __version__
        print(f"crm {__version__}")
        return
    # Parse --data flag before anything else
    if len(argv) >= 2 and argv[0] == "--data":
        storage.use_local_path(argv[1])
        argv = argv[2:]

    if not argv:
        cmd_dashboard([])
        return

    cmd = argv[0]
    args = argv[1:]

    commands = {
        "list": cmd_list,
        "ls": cmd_list,
        "show": cmd_show,
        "note": cmd_note,
        "notes": cmd_notes,
        "stage": cmd_stage,
        "next": cmd_next,
        "due": cmd_due,
        "done": cmd_done,
        "followup": cmd_followup,
        "thread": cmd_thread,
        "templates": cmd_templates,
        "add": cmd_add,
        "edit": cmd_edit,
        "rm": cmd_rm,
        "remove": cmd_rm,
        "restore": cmd_restore,
        "search": cmd_search,
        "find": cmd_search,
        "stages": cmd_stages,
        "config": cmd_config,
        "cfg": cmd_config,
        "where": cmd_where,
        "path": cmd_where,
        "update": cmd_update,
        "help": cmd_help,
    }

    if cmd in commands:
        try:
            commands[cmd](args)
        except ConcurrentWriteError as e:
            print(f"\n{RED}Error:{RESET} {e}")
            print("Another device wrote to the same key. Re-run the command to retry.")
            sys.exit(1)
        # Show overdue warning (skip for commands that already show it)
        if cmd not in ("due", "help", "stages", "config", "cfg", "where", "path", "update"):
            try:
                data = load_data()
                tz = get_tz(data)
                today = datetime.now(tz).strftime("%Y-%m-%d")
                n = sum(1 for c in data["contacts"] if c.get("next_date") and c["next_date"] < today)
                if n:
                    print(f"{RED}! {n} overdue contact{'s' if n != 1 else ''}. Run {RESET}crm due{RED} to review.{RESET}")
            except Exception:
                pass
    else:
        print(f"Unknown command: {cmd}")
        print(__doc__)

if __name__ == "__main__":
    main()
