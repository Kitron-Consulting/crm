"""Tests for `crm config edit` — the JSON-in-$EDITOR round-trip that lets you
set nested config blocks (e.g. smtp/imap oauth-ms) against any backend.
"""

import json
import sys

import pytest

from crm import cli, storage


@pytest.fixture(autouse=True)
def _reset_backend():
    saved = storage._backend
    yield
    storage._backend = saved


def _setup(tmp_path, monkeypatch, initial_cfg, editor_returns):
    """Seed a temp SQLite backend and stub edit_text with the given editor outputs."""
    storage.use_local_path(tmp_path / "crm.db")
    db = storage.open_db()
    for k, v in initial_cfg.items():
        db.set_config(k, v)
    storage.push_db(db)
    storage.close_db(db)

    returns = list(editor_returns)
    monkeypatch.setattr(cli, "edit_text", lambda **kw: returns.pop(0))


def _cfg():
    db = storage.open_db()
    try:
        return db.all_config()
    finally:
        storage.close_db(db)


def test_adds_nested_oauth_block(tmp_path, monkeypatch):
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
    _setup(tmp_path, monkeypatch, {"timezone": "UTC+03:00"}, [json.dumps(new_cfg)])
    cli.cmd_config_edit([])
    cfg = _cfg()
    assert cfg["smtp"]["auth"] == "oauth-ms"
    assert cfg["smtp"]["client_id"] == "c"


def test_cancel_leaves_config_untouched(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch, {"timezone": "UTC"}, [None])  # empty editor = cancel
    cli.cmd_config_edit([])
    assert _cfg() == {"timezone": "UTC"}


def test_no_changes_does_not_save(tmp_path, monkeypatch):
    cfg = {"timezone": "UTC", "smtp": {"host": "h"}}
    _setup(tmp_path, monkeypatch, cfg, [json.dumps(cfg)])
    cli.cmd_config_edit([])
    assert _cfg() == cfg


def test_invalid_json_non_tty_aborts(tmp_path, monkeypatch):
    monkeypatch.setattr(sys.stdin, "isatty", lambda: False)
    _setup(tmp_path, monkeypatch, {"timezone": "UTC"}, ["{not valid json"])
    cli.cmd_config_edit([])
    assert _cfg() == {"timezone": "UTC"}  # aborted, nothing written


def test_invalid_then_fixed_on_reprompt(tmp_path, monkeypatch):
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr(cli, "prompt_confirm", lambda *a, **k: True)
    good = {"timezone": "UTC", "imap": {"auth": "oauth-ms"}}
    _setup(tmp_path, monkeypatch, {"timezone": "UTC"},
           ["{bad json", json.dumps(good)])  # first invalid, then corrected
    cli.cmd_config_edit([])
    assert _cfg()["imap"]["auth"] == "oauth-ms"


def test_non_object_json_rejected(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch, {"timezone": "UTC"}, [json.dumps([1, 2, 3])])
    cli.cmd_config_edit([])
    assert _cfg() == {"timezone": "UTC"}


def test_edit_routed_through_cmd_config(monkeypatch):
    """`crm config edit` must dispatch to the editor, not treat 'edit' as a key."""
    called = {}
    monkeypatch.setattr(cli, "cmd_config_edit", lambda args: called.setdefault("args", args))
    cli.cmd_config(["edit"])
    assert called["args"] == []
