"""Tests for stage history recording and Timeline segment derivation."""

from crm.stages import record_stage_change, stage_segments

TODAY = "2026-09-09"


def test_record_stage_change_appends_and_skips_noop():
    c = {"stage": "contacted"}
    record_stage_change(c, "cold", "contacted", "2026-09-01 10:00")
    record_stage_change(c, "contacted", "contacted", "2026-09-02 10:00")  # no-op
    assert c["stage_history"] == [{"date": "2026-09-01 10:00", "from": "cold", "to": "contacted"}]


def test_segments_from_structured_history_with_creation_entry():
    c = {"stage": "proposal",
         "stage_history": [
             {"date": "2026-08-20 13:05", "from": "", "to": "cold"},          # creation
             {"date": "2026-08-28 09:00", "from": "cold", "to": "contacted"},
             {"date": "2026-09-05 12:00", "from": "contacted", "to": "proposal"},
         ]}
    assert stage_segments(c, TODAY) == [
        {"stage": "cold", "start": "2026-08-20", "end": "2026-08-28"},
        {"stage": "contacted", "start": "2026-08-28", "end": "2026-09-05"},
        {"stage": "proposal", "start": "2026-09-05", "end": None},
    ]


def test_segments_from_notes_fallback_uses_added_note_as_start():
    c = {"stage": "proposal",
         "notes": [  # newest first, as stored
             {"date": "2026-09-05 12:12", "text": "Meeting went well"},
             {"date": "2026-09-01 17:30", "text": "Stage: meeting → proposal"},
             {"date": "2026-08-25 10:00", "text": "Stage: contacted -> meeting"},
             {"date": "2026-08-20 13:05", "text": "Added to CRM"},
         ]}
    assert stage_segments(c, TODAY) == [
        {"stage": "contacted", "start": "2026-08-20", "end": "2026-08-25"},
        {"stage": "meeting", "start": "2026-08-25", "end": "2026-09-01"},
        {"stage": "proposal", "start": "2026-09-01", "end": None},
    ]


def test_no_history_no_notes_is_single_ongoing_segment_from_today():
    assert stage_segments({"stage": "cold"}, TODAY) == [{"stage": "cold", "start": TODAY, "end": None}]


def test_single_segment_from_earliest_note_when_no_moves():
    c = {"stage": "cold", "notes": [{"date": "2026-09-03 08:00", "text": "Imported from sent mail"}]}
    assert stage_segments(c, TODAY) == [{"stage": "cold", "start": "2026-09-03", "end": None}]


def test_history_not_ending_in_actual_stage_is_relabelled_and_flagged():
    # e.g. `crm edit --stage won` wrote no note/history
    c = {"stage": "won",
         "notes": [{"date": "2026-08-25 10:00", "text": "Stage: cold → meeting"},
                   {"date": "2026-08-20 13:05", "text": "Added to CRM"}]}
    segs = stage_segments(c, TODAY)
    assert segs[0] == {"stage": "cold", "start": "2026-08-20", "end": "2026-08-25"}
    assert segs[-1]["stage"] == "won" and segs[-1]["approx"] is True and segs[-1]["end"] is None


def test_structured_history_wins_over_notes():
    c = {"stage": "meeting",
         "stage_history": [{"date": "2026-09-02 09:00", "from": "cold", "to": "meeting"}],
         "notes": [{"date": "2026-09-01 09:00", "text": "Stage: cold → contacted"},   # stale prose
                   {"date": "2026-08-20 09:00", "text": "Added to CRM"}]}
    assert stage_segments(c, TODAY) == [
        {"stage": "cold", "start": "2026-08-20", "end": "2026-09-02"},
        {"stage": "meeting", "start": "2026-09-02", "end": None},
    ]


# --- web integration ----------------------------------------------------------

def _db(contact):
    from crm.db import Db
    db = Db.open_memory()
    db.import_json({"config": {"timezone": "UTC+02:00", "stages": ["cold", "contacted"],
                               "sources": ["cold"]},
                    "contacts": [contact], "removed": []})
    return db


def test_web_state_exposes_segments_and_localised_history():
    from crm import web
    db = _db({"name": "Ada", "email": "", "stage": "contacted", "source": "cold",
              "next_action": "", "next_date": "",
              "notes": [{"date": "2026-08-20 23:30", "text": "Added to CRM"}],
              "stage_history": [{"date": "2026-08-31 23:30", "from": "cold", "to": "contacted"}]})
    c = web.build_state(db)["contacts"][0]
    # UTC 23:30 -> local next day 01:30; segments use local days
    assert c["stage_history"][0]["date"] == "2026-09-01 01:30"
    assert c["segments"] == [{"stage": "cold", "start": "2026-08-21", "end": "2026-09-01"},
                             {"stage": "contacted", "start": "2026-09-01", "end": None}]
    assert "segments" not in db.get_contact(1)   # stored data untouched


def test_web_update_records_structured_history():
    from crm import web
    db = _db({"name": "Ada", "email": "", "stage": "cold", "source": "cold",
              "next_action": "", "next_date": "", "notes": [], "stage_history": []})
    web.api_update_contact(db, {"id": 1, "name": "Ada", "fields": {"stage": "contacted"}})
    h = db.get_contact(1)["stage_history"]
    assert len(h) == 1 and h[0]["from"] == "cold" and h[0]["to"] == "contacted" and len(h[0]["date"]) == 16
    web.api_update_contact(db, {"id": 1, "name": "Ada", "fields": {"role": "CTO"}})   # no stage change
    assert len(db.get_contact(1)["stage_history"]) == 1
