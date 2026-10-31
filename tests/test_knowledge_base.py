"""Tests for the operator knowledge base (knowledge.py, kb_fixtures.py).

Staged fixture world only: invented properties, invented facts, staged
codes. Promotions files live in temp dirs; nothing touches the network.
"""
import json, os, sys, tempfile
from datetime import datetime, timezone
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from jev.kb_fixtures import MAPLE_LAUNDRY_RAW, default_fact_sheet
from jev.knowledge import (FACT_KEYS, KNOWN, OPERATOR_CONFIRMED, UNKNOWN, UNVERIFIED,
                           KnowledgeBase, load_knowledge, scrub_text)

NOW = datetime(2026, 10, 31, 20, 0, tzinfo=timezone.utc)


def _kb():
    return KnowledgeBase(default_fact_sheet())


def _lines(path):
    with open(path, encoding="utf-8") as fh:
        return [json.loads(line) for line in fh if line.strip()]


def test_known_fact_answers():
    kb = _kb()
    assert kb.answer("prop-harbor", "trash_location") == \
        "the outdoor bins by the three-car garage on the far driveway"
    assert kb.answer("prop-maple", "checkin_time") == "4 PM"
    assert kb.answer("prop-harbor", "wifi_password") == "tidepool-staged-4417"


def test_unverified_fact_never_answers():
    kb = _kb()
    assert kb.fact("prop-harbor", "parking_instructions").status == UNVERIFIED
    assert kb.fact("prop-harbor", "parking_instructions").value  # on file, still silent
    assert kb.answer("prop-harbor", "parking_instructions") is None
    assert kb.answer("prop-maple", "trash_location") is None


def test_unknown_and_missing_facts_return_none():
    kb = _kb()
    assert kb.fact("prop-harbor", "tv_details").status == UNKNOWN
    assert kb.answer("prop-harbor", "tv_details") is None
    assert kb.answer("prop-harbor", "coffee_details") is None    # no row at all
    assert kb.answer("prop-nowhere", "trash_location") is None   # no such property


def test_report_shows_all_three_statuses():
    r = _kb().report("prop-harbor")
    assert r["name"] == "Harbor House (staged)"
    assert r[KNOWN] == ["wifi_password", "trash_location"]
    assert r[UNVERIFIED] == ["parking_instructions"]
    assert "tv_details" in r[UNKNOWN] and "coffee_details" in r[UNKNOWN]
    assert {f["status"] for f in r["facts"]} == {KNOWN, UNVERIFIED, UNKNOWN}
    assert [f["key"] for f in r["facts"]] == FACT_KEYS
    parking = next(f for f in r["facts"] if f["key"] == "parking_instructions")
    assert parking["source"] == "knowledge-hub" and parking["value"]


def test_scrub_drops_code_sentences_keeps_ordinary_and_wifi():
    text = ("The lockbox code is 9692. "
            "Bins go out on Tuesday night. "
            "The WiFi password is tidepool-staged-4417.")
    kept = scrub_text(text)
    assert "9692" not in kept and "lockbox" not in kept
    assert "Bins go out on Tuesday night." in kept
    assert "The WiFi password is tidepool-staged-4417." in kept


def test_scrub_catches_dial_format_and_codes_in_words():
    assert scrub_text("Lockbox: 9-6-9-2.") == ""
    assert scrub_text("The gate code is the last four digits of your phone.") == ""
    assert scrub_text("Use keypad 0 0 0 0 at the side door.") == ""
    assert scrub_text("Dial 5551234 for the front desk.") == ""
    assert scrub_text("Use the keypad by the door.") == "Use the keypad by the door."
    assert scrub_text("Check-in is at 4 PM.") == "Check-in is at 4 PM."


def test_wifi_exemption_never_carries_an_entry_code():
    assert scrub_text("WiFi password is tidepool4417 and the door code is 0000.") == ""
    assert scrub_text("WiFi password is tidepool4417, keypad 0-0-0-0.") == ""


def test_fixture_ingest_scrubbed_the_laundry_code():
    assert "0-0-0-0" in MAPLE_LAUNDRY_RAW
    laundry = default_fact_sheet()["properties"]["prop-maple"]["facts"]["laundry_details"]["value"]
    assert "0-0-0-0" not in laundry and "code" not in laundry
    assert laundry == ("The washer and dryer are in the basement laundry room, left side. "
                       "Detergent is on the shelf above the washer.")
    # A raw sheet that skipped the fixture scrub is still scrubbed by the KnowledgeBase.
    raw = default_fact_sheet()
    raw["properties"]["prop-maple"]["facts"]["laundry_details"]["value"] = MAPLE_LAUNDRY_RAW
    assert "0-0-0-0" not in KnowledgeBase(raw).answer("prop-maple", "laundry_details")


def test_promote_flips_unverified_to_known_and_survives_rebuild():
    base = default_fact_sheet()
    kb = KnowledgeBase(base)
    value = "Park in the right half of the driveway."
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "promotions.jsonl")
        line = kb.promote("prop-harbor", "parking_instructions", value, "operator", path, now=NOW)

        f = kb.fact("prop-harbor", "parking_instructions")
        assert (f.status, f.source) == (KNOWN, OPERATOR_CONFIRMED)
        assert kb.answer("prop-harbor", "parking_instructions") == value

        lines = _lines(path)
        assert lines == [line]
        assert lines[0] == {"property_id": "prop-harbor", "fact_key": "parking_instructions",
                            "value": value, "confirmed_by": "operator",
                            "promoted_at": "2026-10-31T20:00:00+00:00"}

        # The base sheet itself is untouched; the overlay carries the confirmation.
        assert base["properties"]["prop-harbor"]["facts"]["parking_instructions"]["status"] == UNVERIFIED
        assert KnowledgeBase(default_fact_sheet()).answer("prop-harbor", "parking_instructions") is None
        rebuilt = load_knowledge(default_fact_sheet(), path)
        assert rebuilt.fact("prop-harbor", "parking_instructions").status == KNOWN
        assert rebuilt.answer("prop-harbor", "parking_instructions") == value
        assert rebuilt.answer("prop-maple", "trash_location") is None   # others unchanged


def test_later_promotion_wins_and_default_clock_is_utc():
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "promotions.jsonl")
        kb = _kb()
        kb.promote("prop-maple", "trash_location", "Bins are in the alley.", "operator", path, now=NOW)
        line = kb.promote("prop-maple", "trash_location", "Bins are by the side gate.", "operator", path)
        assert line["promoted_at"].endswith("+00:00")
        assert len(_lines(path)) == 2
        assert load_knowledge(default_fact_sheet(), path).answer("prop-maple", "trash_location") == \
            "Bins are by the side gate."


def test_missing_promotions_file_is_no_overlay():
    with tempfile.TemporaryDirectory() as d:
        kb = load_knowledge(default_fact_sheet(), os.path.join(d, "absent.jsonl"))
    assert kb.answer("prop-harbor", "parking_instructions") is None
    assert load_knowledge(default_fact_sheet()).answer("prop-maple", "checkin_time") == "4 PM"


def test_promote_refuses_a_code_and_writes_nothing():
    kb = _kb()
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "promotions.jsonl")
        try:
            kb.promote("prop-maple", "entry_method", "Keypad at the side door, code 0000.", "operator", path)
            assert False, "a code-bearing value was promoted"
        except ValueError as e:
            assert "code" in str(e)
        assert not os.path.exists(path)
    assert kb.fact("prop-maple", "entry_method").status == UNKNOWN
