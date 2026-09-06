"""Tests for `crm config edit` — the JSON-in-$EDITOR round-trip that lets you
set nested config blocks (e.g. smtp/imap oauth-ms) against any backend.
"""

import json
import sys

from crm import cli


def _setup(monkeypatch, initial_cfg, editor_returns):
    """Wire load_data/save_data/edit_text and capture what gets saved."""
    data = {"config": dict(initial_cfg), "contacts": []}
    saved = {}

    monkeypatch.setattr(cli, "load_data", lambda: data)
    monkeypatch.setattr(cli, "save_data", lambda d: saved.update(d))

    returns = list(editor_returns)
    monkeypatch.setattr(cli, "edit_text", lambda **kw: returns.pop(0))
    return data, saved


def test_adds_nested_oauth_block(monkeypatch):
    new_cfg = {
        "timezone": "UTC+03:00",
        "smtp": {
            "auth": "oauth-ms",
            "host": "smtp.office365.com",
            "user": "me@x.com",
            "client_id": "c",
            "tenant_id": "t",
        },
    }
    _, saved = _setup(
        monkeypatch,
        {"timezone": "UTC+03:00"},
        [json.dumps(new_cfg)],
    )
    cli.cmd_config_edit([])
    assert saved["config"]["smtp"]["auth"] == "oauth-ms"
    assert saved["config"]["smtp"]["client_id"] == "c"


def test_cancel_leaves_config_untouched(monkeypatch):
    _, saved = _setup(monkeypatch, {"timezone": "UTC"}, [None])  # empty editor = cancel
    cli.cmd_config_edit([])
    assert saved == {}  # save_data never called


def test_no_changes_does_not_save(monkeypatch):
    cfg = {"timezone": "UTC", "smtp": {"host": "h"}}
    _, saved = _setup(monkeypatch, cfg, [json.dumps(cfg)])
    cli.cmd_config_edit([])
    assert saved == {}


def test_invalid_json_non_tty_aborts(monkeypatch):
    monkeypatch.setattr(sys.stdin, "isatty", lambda: False)
    _, saved = _setup(monkeypatch, {"timezone": "UTC"}, ["{not valid json"])
    cli.cmd_config_edit([])
    assert saved == {}  # aborted, nothing written


def test_invalid_then_fixed_on_reprompt(monkeypatch):
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr(cli, "prompt_confirm", lambda *a, **k: True)
    good = {"timezone": "UTC", "imap": {"auth": "oauth-ms"}}
    _, saved = _setup(
        monkeypatch,
        {"timezone": "UTC"},
        ["{bad json", json.dumps(good)],  # first invalid, then corrected
    )
    cli.cmd_config_edit([])
    assert saved["config"]["imap"]["auth"] == "oauth-ms"


def test_non_object_json_rejected(monkeypatch):
    _, saved = _setup(monkeypatch, {"timezone": "UTC"}, [json.dumps([1, 2, 3])])
    cli.cmd_config_edit([])
    assert saved == {}


def test_edit_routed_through_cmd_config(monkeypatch):
    """`crm config edit` must dispatch to the editor, not treat 'edit' as a key."""
    called = {}
    monkeypatch.setattr(cli, "cmd_config_edit", lambda args: called.setdefault("args", args))
    cli.cmd_config(["edit"])
    assert called["args"] == []
