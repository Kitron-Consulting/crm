"""Accounts entity: auto-linking from the company name, sibling queries, and
editable account records (rename cascade, domain/notes)."""

import pytest

from crm.db import Db


def _db():
    return Db.open_memory()


# ---------------- auto-linking ----------------

def test_add_contact_creates_and_links_account():
    db = _db()
    c = db.add_contact({"name": "Ada", "company": "Acme"})
    assert c["account_id"] is not None
    acc = db.get_account(c["account_id"])
    assert acc["name"] == "Acme"


def test_same_company_shares_one_account_case_insensitive():
    db = _db()
    a = db.add_contact({"name": "Ada", "company": "Acme"})
    b = db.add_contact({"name": "Bo", "company": "acme"})     # different casing
    assert a["account_id"] == b["account_id"]
    assert len(db.list_accounts()) == 1
    # canonical name (first spelling) is written back onto both contacts
    assert db.get_contact(b["id"])["company"] == "Acme"


def test_blank_company_leaves_account_null():
    db = _db()
    c = db.add_contact({"name": "Solo", "company": ""})
    assert c["account_id"] is None
    assert db.list_accounts() == []


def test_update_company_relinks_and_clear_unlinks():
    db = _db()
    c = db.add_contact({"name": "Ada", "company": "Acme"})
    db.update_contact(c["id"], {"company": "Globex"})
    got = db.get_contact(c["id"])
    assert got["company"] == "Globex"
    assert db.get_account(got["account_id"])["name"] == "Globex"
    # clearing the company unlinks
    db.update_contact(c["id"], {"company": ""})
    assert db.get_contact(c["id"])["account_id"] is None


# ---------------- queries ----------------

def test_list_accounts_counts_active_contacts_only():
    db = _db()
    db.add_contact({"name": "Ada", "company": "Acme"})
    b = db.add_contact({"name": "Bo", "company": "Acme"})
    db.add_contact({"name": "Cy", "company": "Globex"})
    db.remove_contact(b["id"])                      # soft-deleted -> not counted
    counts = {a["name"]: a["contact_count"] for a in db.list_accounts()}
    assert counts == {"Acme": 1, "Globex": 1}


def test_account_contacts_lists_siblings():
    db = _db()
    a = db.add_contact({"name": "Ada", "company": "Acme", "role": "CTO"})
    db.add_contact({"name": "Bo", "company": "Acme"})
    sibs = db.account_contacts(a["account_id"])
    assert [s["name"] for s in sibs] == ["Ada", "Bo"]      # name-sorted
    assert sibs[0]["role"] == "CTO"


# ---------------- editable records ----------------

def test_update_account_domain_and_notes():
    db = _db()
    c = db.add_contact({"name": "Ada", "company": "Acme"})
    aid = c["account_id"]
    acc = db.update_account(aid, {"domain": "acme.fi", "notes": "Key logo account."})
    assert acc["domain"] == "acme.fi" and acc["notes"] == "Key logo account."


def test_rename_account_cascades_to_contacts():
    db = _db()
    a = db.add_contact({"name": "Ada", "company": "Acme"})
    b = db.add_contact({"name": "Bo", "company": "Acme"})
    db.update_account(a["account_id"], {"name": "Acme Corp"})
    assert db.get_contact(a["id"])["company"] == "Acme Corp"
    assert db.get_contact(b["id"])["company"] == "Acme Corp"


def test_rename_collision_raises():
    db = _db()
    a = db.add_contact({"name": "Ada", "company": "Acme"})
    db.add_contact({"name": "Cy", "company": "Globex"})
    with pytest.raises(ValueError, match="already named"):
        db.update_account(a["account_id"], {"name": "Globex"})


def test_update_missing_account_returns_none():
    assert _db().update_account(999, {"domain": "x"}) is None


# ---------------- import ----------------

# ---------------- web wiring ----------------

def _web_db():
    db = Db.open_memory()
    db.set_config("timezone", "UTC")
    db.set_config("stages", ["cold"])
    db.set_config("sources", ["cold"])
    return db


def test_build_state_includes_accounts_and_contact_account_id():
    from crm import web
    db = _web_db()
    a = db.add_contact({"name": "Ada", "company": "Acme", "stage": "cold"})
    db.add_contact({"name": "Bo", "company": "Acme", "stage": "cold"})
    s = web.build_state(db)
    assert [x["name"] for x in s["accounts"]] == ["Acme"]
    assert s["accounts"][0]["contact_count"] == 2
    assert s["contacts"][0]["account_id"] == a["account_id"]


def test_api_update_account_endpoint():
    from crm import web
    db = _web_db()
    c = db.add_contact({"name": "Ada", "company": "Acme", "stage": "cold"})
    res = web.api_update_account(db, {"id": c["account_id"],
                                      "fields": {"name": "Acme Corp", "domain": "acme.fi"}})
    assert res["account"]["name"] == "Acme Corp" and res["account"]["domain"] == "acme.fi"
    assert db.get_contact(c["id"])["company"] == "Acme Corp"       # cascade
    with pytest.raises(web.StaleError):
        web.api_update_account(db, {"id": 999, "fields": {"domain": "x"}})
    with pytest.raises(web.StaleError):
        web.api_update_account(db, {"fields": {"domain": "x"}})     # missing id


def test_import_json_builds_accounts():
    db = _db()
    db.import_json({"config": {}, "removed": [], "contacts": [
        {"name": "Ada", "company": "Acme", "notes": [], "stage_history": []},
        {"name": "Bo", "company": "acme", "notes": [], "stage_history": []},
        {"name": "Cy", "company": "Globex", "notes": [], "stage_history": []},
        {"name": "Solo", "company": "", "notes": [], "stage_history": []},
    ]})
    accts = {a["name"]: a["contact_count"] for a in db.list_accounts()}
    assert accts == {"Acme": 2, "Globex": 1}          # acme folded into Acme, Solo unlinked
    assert db.get_contact(4)["account_id"] is None     # Solo
