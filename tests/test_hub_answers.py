"""Tests for hub answers (hub_answers.py, hub_fixtures.py). Pseudonym
fixture world only: invented facts, staged codes, no network."""
import ast
import json
import os
import re
import sys
from datetime import timedelta

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from jev import hub_answers as H
from jev import hub_fixtures as F

SRC = os.path.join(os.path.dirname(__file__), "..", "src", "jev")


def _set(pid, md=None, facts=None, now=F.FIXTURE_NOW):
    sheet = F.fact_sheet()["properties"][pid]["facts"] if facts is None else facts
    return H.build_source_set(pid, H.parse_hub_markdown(F.HUB_MARKDOWN[pid] if md is None else md), sheet, now)


def _plan(msg, pid, **kw):
    kw.setdefault("reservation", F.ACCEPTED)
    kw.setdefault("scope", F.SINGLE_UNITS)
    kw.setdefault("excluded", (F.CEDAR_RETREAT,))
    return H.plan(msg, pid, kw.pop("source_set", None) or _set(pid), **kw)


def test_parser_reads_tags_and_multiline_items():
    items = H.parse_hub_markdown(F.HUB_MARKDOWN[F.CEDAR_1A])
    laundry = [i for i in items if i.heading == "Laundry"]
    assert len(laundry) == 1 and laundry[0].tag == "edited"
    assert laundry[0].text.splitlines()[0] == "Laundry Room:" and "dryer sheets" in laundry[0].text
    assert {i.tag for i in items} == {"edited", "auto"}
    two = [i for i in items if i.heading == "Rules & Regulations"]
    assert len(two) == 2  # two items under one heading stay two items


def test_auto_items_and_unconfirmed_or_expired_facts_are_never_sources():
    s1a, s2b = _set(F.CEDAR_1A), _set(F.CEDAR_2B)
    assert all(src.kind in ("hub", "fact") for src in s1a.sources)
    assert not [src for src in s1a.sources if src.label in ("TV", "Heating System")]
    assert ("fact tv_details", "no operator confirmation of the current value") in s1a.skipped
    assert ("fact tv_details", "confirmation older than 7 days") in s2b.skipped
    assert _plan("Does the TV have Netflix?", F.CEDAR_1A).reason == "missing"   # only an [auto] item says so
    assert _plan("Does the TV have Netflix?", F.CEDAR_2B).reason == "missing"   # expired confirmation
    assert _plan("Is there a blender?", F.CEDAR_2B).verdict == "hold"           # [auto] blender


def test_a_changed_fact_value_voids_its_confirmation():
    facts = F.fact_sheet()["properties"][F.CEDAR_1B]["facts"]
    facts["smoking_policy"]["value"] = "Smoking is fine on the patio."
    s = H.build_source_set(F.CEDAR_1B, [], facts, F.FIXTURE_NOW)
    assert s.sources == () and s.skipped[0][1] == "no operator confirmation of the current value"


def test_codes_and_barred_text_never_reach_the_snapshot():
    blob = json.dumps([_set(pid).to_dict() for pid in F.SINGLE_UNITS])
    blob = re.sub(r"[0-9a-f]{12,64}", "<hash>", blob)  # hashes and ids are not text
    # codes, the street number, and the fee never appear; barred statements
    # keep only their claim words (digits dropped) as conflict evidence
    for secret in ("4-1-7-7", "4177", "9-6-9-2", "9692", "5512", "412", "$40", "40 per stay", "up to four"):
        assert secret not in blob, secret
    assert not [st.text for src in _set(F.CEDAR_1A).sources for st in src.statements
                if st.text and ("lockbox" in st.text or "code" in st.text)]
    s = _set(F.CEDAR_1A)
    laundry = next(src for src in s.sources if src.label == "Laundry")
    ev = [st for st in laundry.statements if st.text is None]
    assert len(ev) == 1 and "code" in ev[0].barred and "#" in ev[0].anchors  # numbers kept as a marker only


def test_barred_headings_never_render():
    s = _set(F.CEDAR_1A)
    for label in ("Pet Policy", "Parking", "Emergency keys", "Arrival"):
        src = next(x for x in s.sources if x.label == label)
        assert src.renderable() == [] and all("heading" in st.barred for st in src.statements)


def test_scrub_rules_one_by_one():
    cases = {
        "The laundry room door code is 1-2-3-4.": "code",
        "The keypad is by the side door.": "code",
        "The building is at 77 Elm Grove Road.": "address",
        "Dogs are welcome.": "pets",
        "The unit sleeps up to six.": "occupancy",
        "Park on the street out front.": "parking",
        "Detergent is complimentary.": "money",
        "The fire extinguisher is under the sink.": "safety",
        "Late checkout may be requested.": "stay_change",
        "Call us at any time.": "contact",
        "Trash goes out back — by the garage.": "dash",
        "The bins are by gate 4417.": "code_shaped",
    }
    for text, rule in cases.items():
        assert rule in H.barred_reasons(text), (text, H.barred_reasons(text))
    assert H.barred_reasons("Extra bath towels are on the top shelf of the bathroom closet.") == []
    assert H.barred_reasons("Place used towels in the bathtub.") == []  # "towels" is not "tow"


def test_pronoun_sentence_rides_with_its_antecedent_and_drops_with_it():
    md = ("## Laundry\n- [edited] The laundry room is shared. It is at the back of the building.\n"
          "- [edited] The washer uses code 1-2-3-4. It is in the basement.\n")
    s = H.build_source_set(F.CEDAR_1A, H.parse_hub_markdown(md), {}, F.FIXTURE_NOW)
    first, second = s.sources
    assert [st.text for st in first.statements] == ["The laundry room is shared. It is at the back of the building."]
    assert [st.text for st in second.statements] == [None, None]  # the code sentence and its "It"


def test_answers_are_the_source_statement_verbatim():
    expect = {
        (F.CEDAR_1A, "Where does the trash go?"):
            "Trash goes in the gray bins on the side of the garage, next to the recycling cart.",
        (F.CEDAR_1A, "Is there a coffee maker?"): "The kitchen has a drip coffee maker and an electric kettle.",
        (F.CEDAR_1B, "Where are the extra towels?"): "Extra bath towels are on the top shelf of the bathroom closet.",
        (F.CEDAR_2A, "Where is the laundry room?"):
            "The laundry room is shared with the other units. It is at the front of the building, on the left side "
            "when you face the house.",
        (F.CEDAR_2B, "Is there a microwave?"): "The kitchen has a microwave, a toaster, and a full-size oven.",
    }
    for (pid, msg), statement in expect.items():
        p = _plan(msg, pid)
        assert p.verdict == "answer" and p.text == "Hi! " + statement, (pid, msg, p)
        assert p.sha256 == H.sha256(p.text) and p.source_digest == _set(pid).digest


def test_conflicting_sources_hold_and_agreeing_sources_send_the_corroborated_one():
    p = _plan("Where does the trash go?", F.CEDAR_1B)
    assert p.verdict == "hold" and p.reason == "conflict"
    assert _plan("Is there a coffee maker?", F.CEDAR_2A).reason == "conflict"
    p = _plan("Where does the trash go?", F.CEDAR_2B)  # hub + confirmed fact agree
    assert p.text == "Hi! Trash and recycling go in the blue bins at the end of the driveway."
    assert len(p.source_ids) == 2


def test_consistency_rule():
    assert H._consistent({"trash", "bin", "blue", "end", "driveway"}, {"trash", "bin", "blue", "end", "driveway",
                                                                        "lid", "closed"})
    assert not H._consistent({"trash", "bin", "alley", "behind"}, {"trash", "bin", "front", "curb"})
    assert not H._consistent({"bin", "gray"}, {"bin", "gray", "behind", "garage"})  # an extra place is a claim
    assert not H._consistent({"smoking"}, {"smoking", "not"})                       # a negation is a claim


def test_a_barred_statement_still_counts_as_evidence():
    md = ("## Trash\n- [edited] Trash goes in the gray bins by the garage.\n"
          "## Parking\n- [edited] Park by the alley, where the trash bins are kept.\n")
    s = H.build_source_set(F.CEDAR_1A, H.parse_hub_markdown(md), {}, F.FIXTURE_NOW)
    p = H.plan("Where does the trash go?", F.CEDAR_1A, s, reservation=F.ACCEPTED)
    assert p.verdict == "hold" and p.reason == "conflict"


def test_named_items_must_be_in_the_answer():
    p = _plan("Is there a dryer?", F.CEDAR_1A)  # 1A's statement names the laundry room, not a dryer
    assert p.verdict == "hold" and p.reason == "item_not_covered"
    assert _plan("Is there a dryer?", F.CEDAR_1B).verdict == "answer"


def test_pinned_library_topics_and_barred_subjects_never_get_a_hub_answer():
    for msg in ("What's the wifi password?", "What time is check in?", "When is checkout?",
                "What's the laundry room code?", "Where is the lockbox?", "Where can we park?",
                "Are dogs allowed?", "How many guests can stay?", "Is there a cleaning fee?",
                "Can we check out late?", "What's the address?"):
        p = _plan(msg, F.CEDAR_1A)
        assert p.verdict == "hold", (msg, p)


def test_riders_and_whole_message_coverage_hold():
    for msg in ("Where does the trash go? The power went out.",
                "Where does the trash go? Also my son has a fever",
                "Where does the trash go? \U0001F6A8",
                "Where does the trash go? Can you send more bags?",
                "Where does the trash go and where is the laundry?",
                "We are leaving tomorrow. Where does the trash go?"):
        assert _plan(msg, F.CEDAR_1A).verdict == "hold", msg
    assert _plan("Hi! Where does the trash go? Thanks so much \U0001F60A", F.CEDAR_1A).verdict == "answer"


def test_scope_status_and_the_retreat():
    assert _plan("Where does the trash go?", F.CEDAR_RETREAT).reason == "scope"
    assert _plan("Where does the trash go?", F.CEDAR_1A, reservation={"status": "inquiry"}).reason == "reservation"
    assert _plan("Where does the trash go?", F.CEDAR_1A, reservation=None).reason == "reservation"
    s = _set(F.CEDAR_1A)
    assert H.plan("Where does the trash go?", F.CEDAR_1B, s, reservation=F.ACCEPTED).reason == "no_sources"


def test_snapshot_round_trip_and_tamper_refusal():
    s = _set(F.CEDAR_1A)
    doc = json.loads(json.dumps(s.to_dict()))
    assert H.source_set_from_dict(doc).digest == s.digest
    doc["sources"][1]["statements"][0]["text"] = "Trash goes in the neighbor's yard."
    try:
        H.source_set_from_dict(doc)
        raise AssertionError("tampered snapshot accepted")
    except ValueError as e:
        assert "digest" in str(e)


def test_confirmation_window_is_seven_days():
    later = F.FIXTURE_NOW + timedelta(days=7, hours=1)
    s = _set(F.CEDAR_2B, now=later)
    assert ("fact trash_location", "confirmation older than 7 days") in s.skipped
    p = H.plan("Where does the trash go?", F.CEDAR_2B, s, reservation=F.ACCEPTED)
    assert p.verdict == "answer" and len(p.source_ids) == 1  # the hub item alone still answers


def test_engines_make_no_model_call():
    """No import of any model client from the answer engines or ratings."""
    for name in ("hub_answers.py", "geo_answers.py", "answer_text.py", "ratings.py"):
        tree = ast.parse(open(os.path.join(SRC, name), encoding="utf-8").read())
        mods = {a.name for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names}
        mods |= {n.module or "" for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)}
        assert not {m for m in mods if any(k in m for k in ("client", "baseten", "openai", "anthropic", "prompts"))}, \
            (name, mods)
