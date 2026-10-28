import json, math, os, sys

import pytest
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
os.environ["JEV_SPEND_STATE"] = os.path.join(os.path.dirname(__file__), "spend-plays-test.json")

from jev.plays import (FixtureContext, PlayEngine, STAKES,
                       default_fixture_data, get, match)
from jev.plays.camera_check import (DOG_REASON, MOVE_IN_MIN_DAYS,
                                    CameraCheck,
                                    CameraOffline, CameraUpkeep,
                                    GuestLockout, GuestUnsafe,
                                    MorningCheck, PostTurnoverCheck,
                                    SunsetCheck, VendorProof,
                                    _NOTIFIED_CLUSTERS,
                                    _reset_door_frames,
                                    _reset_gathering_frames,
                                    _reset_move_in_signs,
                                    _reset_outage_correlator,
                                    gathering_margin,
                                    handle_camera_event,
                                    handle_camera_offline_event,
                                    handle_camera_online_event,
                                    handle_camera_upkeep_event,
                                    handle_guest_lockout_event,
                                    handle_guest_unsafe_event,
                                    handle_morning_check_event,
                                    handle_offline_burst,
                                    handle_post_turnover_event,
                                    handle_sunset_check_event,
                                    handle_vendor_window_closed_event,
                                    handle_vendor_window_event)
from jev.plays.vision import FixtureVision, STAGED_FRAMES, parse_classification
from jev.types import Category, Decision


def _engine(ledger=None, log_path=None, data=None):
    return PlayEngine(FixtureContext(data or default_fixture_data()),
                      ledger=ledger or {}, log_path=log_path)


def _event(engine, rec_frame, play=None, **over):
    # rec_frame picks the staged record the payload defaults come
    # from; any payload field (frame_id included) may be overridden.
    rec = STAGED_FRAMES[rec_frame]
    payload = {"property_id": rec["property_id"], "date": rec["date"],
               "camera": rec["camera"], "ts": rec["ts"],
               "frame_id": rec_frame}
    payload.update(over)
    return handle_camera_event(engine, payload, play=play)


def _step(run, name):
    return next(s for s in run.steps if s.name == name)


def test_notify_operator_is_a_middle_stakes_key():
    assert STAKES["notify_operator"] == "middle"


def test_registered_but_never_message_triggered():
    assert get("camera_check") is not None
    # Event-triggered only: no triage Decision starts this play, and
    # decision matching is unchanged by its registration.
    assert match(Decision(Category.ACT, 0.9),
                 "There are people outside, the camera went off", {}) is None


def test_expected_guest_arrival_logs_only():
    run = _event(_engine(), "f1")
    assert run.status == "completed" and run.data["outcome"] == "logged"
    assert _step(run, "wait_camera_motion").status == "done"
    reality = _step(run, "match_reality")
    assert reality.result["arrival_today"] is True
    classify = _step(run, "classify_frame")
    assert classify.result["scene"] == "expected_guest"
    assert classify.result["route"] == "log"
    log_step = _step(run, "log_event")
    assert log_step.action_key == "auto_close" and log_step.stake == "low"
    assert log_step.result["logged"] is True
    assert _step(run, "notify_operator").status == "skipped"
    # This play never carries a guest-facing action, on any path.
    assert all(s.action_key != "message_guest" for s in run.steps)


def test_cleaner_inside_cleaning_window_logs():
    run = _event(_engine(), "f2")
    assert run.status == "completed" and run.data["outcome"] == "logged"
    classify = _step(run, "classify_frame")
    assert classify.result["scene"] == "cleaner"
    assert classify.result["route"] == "log"
    assert _step(run, "notify_operator").status == "skipped"


def test_trivial_scenes_log_only():
    vision = FixtureVision(staged={
        "fx-none": {"scene": "nothing_notable", "people_count": 0,
                    "note": "Empty driveway."},
        "fx-cat": {"scene": "animal", "people_count": None,
                   "note": "A cat crosses the driveway."},
        "fx-car": {"scene": "vehicle", "people_count": None,
                   "note": "A delivery van idles at the curb."},
    })
    for frame_id in ("fx-none", "fx-cat", "fx-car"):
        run = _event(_engine(), "f1", play=CameraCheck(vision=vision),
                     property_id="cedar-1a", date="2026-10-28",
                     frame_id=frame_id)
        assert run.status == "completed" and run.data["outcome"] == "logged"
        assert _step(run, "log_event").result["logged"] is True
        assert _step(run, "notify_operator").status == "skipped"


def test_large_gathering_notifies_operator_held_at_p1(tmp_path):
    # Case #6 (batch 3): the gathering notification rides on the
    # stay's sustained estimate. f26 and f27 come first that
    # evening and log; f3 completes the sustained run and notifies.
    _reset_gathering_frames()
    log = str(tmp_path / "runs.jsonl")
    engine = _engine(log_path=log)
    for earlier in ("f26", "f27"):
        assert _event(engine, earlier).data["outcome"] == "logged"
    run = _event(engine, "f3")
    assert run.status == "completed"
    assert run.data["outcome"] == "notify_operator"
    reality = _step(run, "match_reality")
    assert reality.result["mid_stay"] is True
    assert reality.result["expected_party"] == 4
    classify = _step(run, "classify_frame")
    assert classify.result["scene"] == "large_gathering"
    assert classify.result["people_count"] == 11
    assert classify.result["route"] == "notify"
    assert classify.result["gathering"]["floor"] == 9
    assert classify.result["gathering"]["threshold"] == 8

    notify = _step(run, "notify_operator")
    assert notify.action_key == "notify_operator"
    assert notify.stake == "middle"
    summary = notify.result["summary"]
    assert "Garden Unit 1A" in summary
    # The claim is the sustained floor as a model estimate — never
    # one frame's count, never a census.
    assert "at least 9 people (model estimate" in summary
    assert "not a headcount" in summary
    assert "about 11" not in summary and "eleven" not in summary.lower()
    assert "(11 people)" not in summary
    assert "f26 (9:20 PM), f27 (9:35 PM), f3 (9:48 PM)" in summary
    assert "party of 4" in summary
    assert "Nothing was sent to the guest" in summary
    assert notify.result["at_least"] == 9
    # Middle right, not earned in an empty ledger: held, nothing sent.
    assert notify.required_right == "approve_first"
    assert notify.disposition == "hold" and notify.status == "held"
    assert notify.result["executed"] is False
    assert run.held_steps == ["notify_operator"]
    assert _step(run, "log_event").status == "skipped"

    with open(log) as fh:
        records = [json.loads(line) for line in fh if line.strip()]
    assert any(r["event"] == "gate" and r["action_key"] == "notify_operator"
               and r["disposition"] == "hold" for r in records)
    assert records[-1]["event"] == "run_completed"


def test_unclear_frame_fails_safe_to_notify():
    run = _event(_engine(), "f4")
    assert run.status == "completed"
    assert run.data["outcome"] == "notify_operator"
    classify = _step(run, "classify_frame")
    assert classify.result["scene"] == "unclear"
    assert classify.result["route"] == "notify"
    assert "unclear" in classify.result["reason"]
    notify = _step(run, "notify_operator")
    assert notify.status == "held"
    assert "too dark or unclear" in notify.result["summary"]


class _NoVision:
    def classify(self, frame_id, **_):
        return None


class _BoomVision:
    def classify(self, frame_id, **_):
        raise RuntimeError("vision backend down")


def test_vision_failure_fails_safe_to_notify():
    for vision in (_NoVision(), _BoomVision()):
        run = _event(_engine(), "f1", play=CameraCheck(vision=vision))
        assert run.status == "completed"
        assert run.data["outcome"] == "notify_operator"
        classify = _step(run, "classify_frame")
        assert classify.result["scene"] == "unclassified"
        assert classify.result["route"] == "notify"
        assert _step(run, "notify_operator").status == "held"


def test_unknown_frame_id_fails_safe_to_notify():
    run = _event(_engine(), "f1", frame_id="no-such-frame")
    assert run.data["outcome"] == "notify_operator"
    assert _step(run, "classify_frame").result["scene"] == "unclassified"


def test_unknown_person_notifies_even_with_arrival_expected():
    vision = FixtureVision(staged={
        "fx-stranger": {"scene": "unknown_person", "people_count": 1,
                        "note": "A person trying the side gate."}})
    run = _event(_engine(), "f1", play=CameraCheck(vision=vision),
                 frame_id="fx-stranger")
    assert run.data["outcome"] == "notify_operator"
    assert _step(run, "notify_operator").status == "held"


def test_notify_executes_simulated_when_right_earned():
    _reset_gathering_frames()
    ledger = {"categories": {"notify_operator": {
        "decisive_n": 30, "decisive_accuracy": 0.96,
        "high_conf_accuracy": 0.99}}}
    engine = _engine(ledger=ledger)
    _event(engine, "f26")
    _event(engine, "f27")
    run = _event(engine, "f3")
    notify = _step(run, "notify_operator")
    assert notify.required_right == "send_with_log"
    assert notify.status == "done"
    assert notify.result["executed"] is True
    assert notify.result["channel"] == "simulated"
    assert run.status == "completed" and run.held_steps == []


def test_unknown_property_event_holds_for_human():
    run = _event(_engine(), "f1", property_id="no-such-property")
    assert run.status == "held_for_human"
    assert run.hold_reason
    assert _step(run, "notify_operator").status == "pending"


def test_malformed_event_is_a_caller_error():
    try:
        handle_camera_event(_engine(), {"property_id": "cedar-2a"})
    except ValueError:
        pass
    else:
        raise AssertionError("expected ValueError for a malformed event")


def test_parse_classification_is_strict():
    ok = parse_classification(
        '{"scene": "vehicle", "people_count": null, "note": "A van."}')
    assert ok is not None and ok.scene == "vehicle"
    assert ok.people_count is None and ok.source == "live"
    # Prose around the JSON is tolerated, like parse_decision.
    ok2 = parse_classification(
        'Here is the frame: {"scene": "animal", "people_count": null,'
        ' "note": "A raccoon."} Done.')
    assert ok2 is not None and ok2.scene == "animal"
    assert parse_classification('{"scene": "spaceship"}') is None
    assert parse_classification(
        '{"scene": "vehicle", "people_count": "several"}') is None
    assert parse_classification('{"scene": "vehicle",'
                                ' "people_count": -1}') is None
    assert parse_classification("no json here") is None
    assert parse_classification("") is None


def test_parse_classification_door_lights_state_fields():
    # The state fields are optional for the motion play (absent ->
    # unknown), validated strictly when present: the post-turnover
    # check stakes a route on them, so a bad value must fail safe.
    ok = parse_classification(
        '{"scene": "nothing_notable", "people_count": 0,'
        ' "note": "Door open.", "door": "open", "lights": "off"}')
    assert ok is not None and ok.door == "open" and ok.lights == "off"
    legacy = parse_classification(
        '{"scene": "vehicle", "people_count": null, "note": "A van."}')
    assert legacy is not None and legacy.door == "unknown"
    assert legacy.lights == "unknown"
    assert parse_classification(
        '{"scene": "nothing_notable", "door": "ajar"}') is None
    assert parse_classification(
        '{"scene": "nothing_notable", "lights": "dim"}') is None
    assert parse_classification(
        '{"scene": "nothing_notable", "door": null}') is None


# -- vacant mode -------------------------------------------------------

def test_vacant_mode_person_notifies_with_frame_reference():
    run = _event(_engine(), "f5")
    assert run.status == "completed"
    assert run.data["outcome"] == "notify_operator"
    reality = _step(run, "match_reality")
    assert reality.result["vacant"] is True
    notify = _step(run, "notify_operator")
    assert notify.status == "held"
    summary = notify.result["summary"]
    assert "closed and should be vacant" in summary
    assert "frame f5" in summary
    assert "still" in summary.lower()
    assert "attached in the simulated" in summary
    assert notify.result["frame_id"] == "f5"


def test_vacant_mode_cleaner_inside_scheduled_window_logs():
    run = _event(_engine(), "f6")
    assert run.status == "completed" and run.data["outcome"] == "logged"
    reality = _step(run, "match_reality")
    assert reality.result["vacant"] is True
    assert reality.result["work_window_active"] is True
    assert _step(run, "classify_frame").result["route"] == "log"
    assert _step(run, "log_event").result["logged"] is True
    assert _step(run, "notify_operator").status == "skipped"


def test_person_present_when_cleaning_window_opens_notifies():
    # Turnover presence corroboration: f9 is a person (not the
    # cleaner) still at the door two minutes after the cleaning
    # window opens. The operator hears the Play 3 warning, not a
    # generic stranger flag.
    run = _event(_engine(), "f9")
    assert run.status == "completed"
    assert run.data["outcome"] == "notify_operator"
    classify = _step(run, "classify_frame")
    assert classify.result["scene"] == "unknown_person"
    assert "guest may not have left yet" in classify.result["reason"]
    notify = _step(run, "notify_operator")
    assert notify.status == "held"
    assert "guest may not have left yet" in notify.result["summary"]


# -- camera offline: the switch play -----------------------------------

def _offline(engine, play=None, **payload):
    base = {"property_id": "cedar-1a", "date": "2026-10-29",
            "camera": "front-door", "ts": "2026-10-29T19:20:00"}
    base.update(payload)
    return handle_camera_offline_event(engine, base, play=play)


def test_offline_during_stay_drafts_switch_message_lights_and_camera():
    run = _offline(_engine())
    assert run.status == "completed"
    assert run.data["outcome"] == "switch_message_held"
    reality = _step(run, "check_reality")
    assert reality.result["stay_in_progress"] is True
    assert reality.result["switch_fed"] is True
    assert reality.result["route"] == "guest"
    assert reality.result["mention"] == "lights_and_camera"
    msg = _step(run, "guest_switch_message")
    assert msg.action_key == "message_guest" and msg.stake == "middle"
    assert msg.disposition == "hold" and msg.status == "held"
    assert msg.result["executed"] is False
    draft = msg.result["draft"]
    assert "by the front door, inside" in draft
    assert "entrance lights and the outside camera" in draft
    assert "flip it back on" in draft
    assert "Thank you" in draft
    assert "Alex" in draft
    assert _step(run, "notify_operator").status == "skipped"


def test_offline_during_stay_lights_only_wording_variant():
    run = _offline(_engine(), mention="lights_only")
    msg = _step(run, "guest_switch_message")
    assert msg.status == "held"
    assert msg.result["mention"] == "lights_only"
    draft = msg.result["draft"]
    assert "entrance lights" in draft
    assert "camera" not in draft.lower()


def test_offline_play_instance_mention_variant():
    run = _offline(_engine(), play=CameraOffline(mention="lights_only"))
    assert _step(run, "guest_switch_message").result["mention"] \
        == "lights_only"


def test_offline_no_stay_notifies_operator_no_guest_draft():
    run = _offline(_engine(), date="2026-10-28")
    assert run.status == "completed"
    assert run.data["outcome"] == "operator_flagged"
    reality = _step(run, "check_reality")
    assert reality.result["stay_in_progress"] is False
    assert reality.result["route"] == "operator"
    assert _step(run, "guest_switch_message").status == "skipped"
    notify = _step(run, "notify_operator")
    assert notify.status == "held"
    assert notify.result["executed"] is False
    assert "No guest message was drafted" in notify.result["summary"]
    assert "maintenance" in notify.result["summary"]


def test_offline_camera_not_switch_fed_notifies_operator():
    run = _offline(_engine(), camera="backyard")
    assert run.data["outcome"] == "operator_flagged"
    reality = _step(run, "check_reality")
    assert reality.result["stay_in_progress"] is True
    assert reality.result["switch_fed"] is False
    assert _step(run, "guest_switch_message").status == "skipped"
    assert _step(run, "notify_operator").status == "held"


def test_camera_back_online_resolves_held_offline_run():
    engine = _engine()
    run = _offline(engine)
    assert _step(run, "guest_switch_message").result["executed"] is False
    resolved = handle_camera_online_event(
        engine, run, {"camera": "front-door",
                      "ts": "2026-10-29T19:26:00"})
    assert resolved.data["outcome"] == "resolved_camera_back"
    assert resolved.data["resolved"] is True
    closing = _step(resolved, "resolve_camera_online")
    assert closing.status == "done"
    assert closing.result["resolved"] is True
    # The held message stays held: it never executed.
    assert _step(resolved, "guest_switch_message").status == "held"


def test_offline_unknown_property_holds_and_bad_variant_raises():
    run = _offline(_engine(), property_id="no-such-property")
    assert run.status == "held_for_human"
    assert _step(run, "guest_switch_message").status == "pending"
    try:
        _offline(_engine(), mention="everything")
    except ValueError:
        pass
    else:
        raise AssertionError("expected ValueError for a bad variant")
    try:
        handle_camera_offline_event(_engine(), {"property_id": "x"})
    except ValueError:
        pass
    else:
        raise AssertionError("expected ValueError for a malformed event")


# -- post-turnover state check -----------------------------------------

def _post(engine, frame, play=None, **over):
    rec = STAGED_FRAMES[frame]
    payload = {"property_id": rec["property_id"], "date": rec["date"],
               "kind": "post_turnover", "camera": rec["camera"],
               "ts": rec["ts"], "frame_id": frame}
    payload.update(over)
    return handle_post_turnover_event(engine, payload, play=play)


def test_post_turnover_door_open_notifies_operator():
    run = _post(_engine(), "f7")
    assert run.status == "completed"
    assert run.data["outcome"] == "notify_operator"
    state = _step(run, "classify_state")
    assert state.result["door"] == "open"
    assert state.result["lights"] == "off"
    assert state.result["route"] == "notify"
    notify = _step(run, "notify_operator")
    assert notify.status == "held"
    assert notify.result["executed"] is False
    assert "door open" in notify.result["summary"]
    assert "Frame: f7" in notify.result["summary"]
    assert _step(run, "log_event").status == "skipped"
    # No guest-facing step anywhere in the state check either; the
    # offline play above is the camera system's only one.
    assert all(s.action_key != "message_guest" for s in run.steps)


def test_post_turnover_all_consistent_logs_only():
    run = _post(_engine(), "f8")
    assert run.status == "completed" and run.data["outcome"] == "logged"
    state = _step(run, "classify_state")
    assert state.result["door"] == "closed"
    assert state.result["lights"] == "off"
    assert state.result["route"] == "log"
    assert _step(run, "log_event").result["logged"] is True
    assert _step(run, "notify_operator").status == "skipped"


def test_post_turnover_unsettled_state_fails_safe_to_notify():
    vision = FixtureVision(staged={
        "fx-state": {"scene": "nothing_notable", "people_count": 0,
                     "note": "Dark frame at the door."}})
    run = _post(_engine(), "f8", play=PostTurnoverCheck(vision=vision),
                frame_id="fx-state")
    assert run.data["outcome"] == "notify_operator"
    assert _step(run, "classify_state").result["route"] == "notify"
    assert _step(run, "notify_operator").status == "held"


def test_post_turnover_malformed_event_is_a_caller_error():
    try:
        handle_post_turnover_event(
            _engine(), {"property_id": "cedar-2a",
                        "date": "2026-10-28", "kind": "weekly",
                        "frame_id": "f8"})
    except ValueError:
        pass
    else:
        raise AssertionError("expected ValueError for a bad kind")


# -- final-list batch: view field + camera covered/turned (case #17) ---

def test_parse_classification_view_field():
    # Optional like door/lights: absent -> unknown, validated
    # strictly when present. A merely unclear frame's view is
    # "unknown" — it must never parse into blocked/shifted.
    ok = parse_classification(
        '{"scene": "vehicle", "people_count": null, "note": "A van.",'
        ' "view": "blocked"}')
    assert ok is not None and ok.view == "blocked"
    legacy = parse_classification(
        '{"scene": "unclear", "people_count": null, "note": "Dark."}')
    assert legacy is not None and legacy.view == "unknown"
    assert parse_classification(
        '{"scene": "vehicle", "view": "tilted"}') is None
    assert parse_classification(
        '{"scene": "vehicle", "view": null}') is None


def test_covered_camera_notifies_urgent_on_one_night_weekend_stay():
    run = _event(_engine(), "f10")
    assert run.status == "completed"
    assert run.data["outcome"] == "notify_operator"
    reality = _step(run, "match_reality")
    assert reality.result["one_night_weekend"] is True
    classify = _step(run, "classify_frame")
    # The scene alone (nothing notable) would have logged; the
    # blocked view outranks it.
    assert classify.result["scene"] == "nothing_notable"
    assert classify.result["view"] == "blocked"
    assert classify.result["route"] == "notify"
    notify = _step(run, "notify_operator")
    assert notify.status == "held"
    assert notify.result["executed"] is False
    assert notify.result["urgent"] is True
    assert "view reads blocked" in notify.result["summary"]
    assert "One-night weekend stay — treat as urgent" \
        in notify.result["summary"]
    assert all(s.action_key != "message_guest" for s in run.steps)


def test_shifted_camera_notifies_without_urgency_off_that_stay_shape():
    run = _event(_engine(), "f11")
    assert run.status == "completed"
    assert run.data["outcome"] == "notify_operator"
    classify = _step(run, "classify_frame")
    assert classify.result["scene"] == "vehicle"
    assert classify.result["view"] == "shifted"
    assert classify.result["route"] == "notify"
    notify = _step(run, "notify_operator")
    assert notify.status == "held"
    assert notify.result["urgent"] is False
    assert "view reads shifted" in notify.result["summary"]


def test_explicitly_normal_view_routes_on_scene_as_before():
    vision = FixtureVision(staged={
        "fx-view-ok": {"scene": "nothing_notable", "people_count": 0,
                       "note": "Empty driveway.", "view": "normal"}})
    run = _event(_engine(), "f1", play=CameraCheck(vision=vision),
                 property_id="cedar-1a", date="2026-10-28",
                 frame_id="fx-view-ok")
    assert run.data["outcome"] == "logged"
    assert _step(run, "classify_frame").result["view"] == "normal"
    assert _step(run, "notify_operator").status == "skipped"


# -- final-list batch: stuck at the door, dwell pattern (case #10b) ----

def test_stuck_at_the_door_dwell_pattern_notifies():
    _reset_door_frames()
    engine = _engine()
    first = _event(engine, "f12")
    # One frame of a person at the shut door, arrival expected:
    # unremarkable, logs like any expected arrival.
    assert first.data["outcome"] == "logged"
    assert _step(first, "classify_frame").result["route"] == "log"
    second = _event(engine, "f13")
    assert second.status == "completed"
    assert second.data["outcome"] == "notify_operator"
    classify = _step(second, "classify_frame")
    assert classify.result["route"] == "notify"
    assert "a person is at the door and the door reads shut" \
        in classify.result["reason"]
    notify = _step(second, "notify_operator")
    assert notify.status == "held"
    assert notify.result["executed"] is False
    assert "a person is at the door and the door reads shut" \
        in notify.result["summary"]
    assert "Nothing was sent to the guest" in notify.result["summary"]


def test_door_open_breaks_the_dwell_pattern():
    _reset_door_frames()
    engine = _engine()
    _event(engine, "f12")
    opened = _event(engine, "f14")
    assert opened.data["outcome"] == "logged"
    classify = _step(opened, "classify_frame")
    assert classify.result["door"] == "open"
    assert "stuck-at-the-door" not in classify.result["reason"]
    assert _step(opened, "log_event").result["logged"] is True


def test_dwell_needs_the_arrival_window():
    _reset_door_frames()
    vision = FixtureVision(staged={
        "fx-dwell-a": {"scene": "unknown_person", "people_count": 1,
                       "note": "A person at the door.",
                       "door": "closed"},
        "fx-dwell-b": {"scene": "unknown_person", "people_count": 1,
                       "note": "Still at the door.", "door": "closed"},
    })
    engine = _engine()
    # Garden Unit 1A on 2026-10-28 has NO arrival: same two-frame
    # pattern, but no arrival window, so both runs take the plain
    # stranger route and neither claims the stuck-at-the-door read.
    for frame_id, ts in (("fx-dwell-a", "2026-10-28T10:00:00"),
                         ("fx-dwell-b", "2026-10-28T10:07:00")):
        run = _event(engine, "f1", play=CameraCheck(vision=vision),
                     property_id="cedar-1a", date="2026-10-28",
                     camera="front-door", ts=ts, frame_id=frame_id)
        assert run.data["outcome"] == "notify_operator"
        reason = _step(run, "classify_frame").result["reason"]
        assert "matches nobody expected" in reason
        assert "stuck-at-the-door" not in reason


# -- final-list batch: guest lockout report (case #10a) -----------------

def _lockout(engine, play=None, **payload):
    base = {"property_id": "maple-house", "date": "2026-10-28",
            "camera": "front-door", "ts": "2026-10-28T16:20:00"}
    base.update(payload)
    return handle_guest_lockout_event(engine, base, play=play)


def test_lockout_report_assembles_latest_door_frame_for_operator():
    run = _lockout(_engine())
    assert run.status == "completed"
    assert run.data["outcome"] == "notify_operator"
    assemble = _step(run, "assemble_door_frame")
    # f18 (4:08 PM) is the latest front-door frame at or before the
    # 4:20 PM report; the evening sunset frames are not picked.
    assert assemble.result["frame_id"] == "f18"
    assert assemble.result["door"] == "closed"
    assert assemble.result["guest"] == "Marcus"
    notify = _step(run, "notify_operator")
    assert notify.action_key == "notify_operator"
    assert notify.status == "held"
    assert notify.result["executed"] is False
    summary = notify.result["summary"]
    assert "a person is at the door and the door reads shut" in summary
    assert "Nothing was sent to the guest" in summary
    # The lockout play never messages the guest either.
    assert all(s.action_key != "message_guest" for s in run.steps)


def test_lockout_report_door_open_reads_open():
    run = _lockout(_engine(), property_id="cedar-2a",
                   frame_id="f14", ts="2026-10-28T15:52:00")
    assemble = _step(run, "assemble_door_frame")
    assert assemble.result["frame_id"] == "f14"
    assert assemble.result["door"] == "open"
    summary = _step(run, "notify_operator").result["summary"]
    assert "a person is at the door and the door reads open" in summary
    assert "door reads shut" not in summary


def test_lockout_unknown_property_holds_and_malformed_raises():
    run = _lockout(_engine(), property_id="no-such-property")
    assert run.status == "held_for_human"
    assert _step(run, "notify_operator").status == "pending"
    try:
        handle_guest_lockout_event(_engine(), {"property_id": "x"})
    except ValueError:
        pass
    else:
        raise AssertionError("expected ValueError for a malformed event")


# -- final-list batch: guest feels unsafe (case #49) --------------------

def _unsafe(engine, play=None, **payload):
    base = {"property_id": "cedar-2a", "date": "2026-10-28",
            "kind": "guest_unsafe"}
    base.update(payload)
    return handle_guest_unsafe_event(engine, base, play=play)


def test_guest_unsafe_assembles_latest_frames_urgent():
    run = _unsafe(_engine())
    assert run.status == "completed"
    assert run.data["outcome"] == "notify_operator"
    assemble = _step(run, "assemble_latest_frames")
    # Studio 2A's three newest frames that day, newest first.
    assert assemble.result["frames"] == ["f16", "f14", "f13"]
    notify = _step(run, "notify_operator")
    assert notify.status == "held"
    assert notify.result["executed"] is False
    assert notify.result["urgent"] is True
    assert notify.result["frames"] == ["f16", "f14", "f13"]
    summary = notify.result["summary"]
    assert summary.startswith("URGENT — guest feels unsafe")
    assert "f16" in summary and "f13" in summary
    assert "Nothing was sent to the guest" in summary
    assert all(s.action_key != "message_guest" for s in run.steps)


def test_guest_unsafe_no_frames_on_record_says_so_plainly():
    run = _unsafe(_engine(), property_id="cedar-1a")
    assert run.status == "completed"
    assert run.data["outcome"] == "notify_operator"
    assemble = _step(run, "assemble_latest_frames")
    assert assemble.result["frames"] == []
    notify = _step(run, "notify_operator")
    assert notify.status == "held"
    summary = notify.result["summary"]
    assert "No camera frames are on record" in summary
    assert "absence is itself the information" in summary


def test_guest_unsafe_bad_kind_and_malformed_raise():
    try:
        _unsafe(_engine(), kind="guest_bored")
    except ValueError:
        pass
    else:
        raise AssertionError("expected ValueError for a bad kind")
    try:
        handle_guest_unsafe_event(_engine(), {"date": "2026-10-28"})
    except ValueError:
        pass
    else:
        raise AssertionError("expected ValueError for a malformed event")


# -- final-list batch: dark entry sunset check (case #11) ---------------

def _sunset(engine, frame, play=None, **over):
    rec = STAGED_FRAMES[frame]
    payload = {"property_id": rec["property_id"], "date": rec["date"],
               "kind": "sunset_check", "camera": rec["camera"],
               "ts": rec["ts"], "frame_id": frame}
    payload.update(over)
    return handle_sunset_check_event(engine, payload, play=play)


def test_sunset_dark_entry_second_night_adds_bulb_change():
    # The fixtures seed Studio 2A with one dark night already, so
    # this dark read is the second night running.
    run = _sunset(_engine(), "f16")
    assert run.status == "completed"
    assert run.data["outcome"] == "notify_operator"
    reality = _step(run, "match_reality")
    assert reality.result["arrival_today"] is True
    assert reality.result["guest"] == "Dana"
    entry = _step(run, "classify_entry")
    assert entry.result["lights"] == "off"
    assert entry.result["dark_streak"] == 2
    assert entry.result["second_night"] is True
    notify = _step(run, "notify_operator")
    assert notify.status == "held"
    assert notify.result["executed"] is False
    summary = notify.result["summary"]
    assert "entry is dark before the guest arrives" in summary
    assert "Second night running — bulb change belongs on the " \
           "next turnover list" in summary
    assert all(s.action_key != "message_guest" for s in run.steps)


def test_sunset_dark_entry_first_night_notifies_without_bulb_line():
    run = _sunset(_engine(), "f15")
    assert run.data["outcome"] == "notify_operator"
    entry = _step(run, "classify_entry")
    assert entry.result["lights"] == "off"
    assert entry.result["dark_streak"] == 1
    assert entry.result["second_night"] is False
    summary = _step(run, "notify_operator").result["summary"]
    assert "entry is dark before the guest arrives" in summary
    assert "bulb change" not in summary


def test_sunset_lights_on_logs_only():
    run = _sunset(_engine(), "f17")
    assert run.status == "completed" and run.data["outcome"] == "logged"
    entry = _step(run, "classify_entry")
    assert entry.result["lights"] == "on"
    assert entry.result["route"] == "log"
    assert entry.result["dark_streak"] == 0
    assert _step(run, "log_event").result["logged"] is True
    assert _step(run, "notify_operator").status == "skipped"


def test_sunset_no_arrival_that_day_logs_and_skips_the_streak():
    vision = FixtureVision(staged={
        "fx-dark-entry": {"scene": "nothing_notable", "people_count": 0,
                          "note": "Entry dark.", "lights": "off"}})
    engine = _engine()
    # Garden Unit 1A on 2026-10-28: nobody arriving, so a dark
    # entry is noted, not flagged — and the streak is not touched.
    run = _sunset(engine, "f15", play=SunsetCheck(vision=vision),
                  property_id="cedar-1a", date="2026-10-28",
                  camera="front-door", ts="2026-10-28T18:50:00",
                  frame_id="fx-dark-entry")
    assert run.data["outcome"] == "logged"
    entry = _step(run, "classify_entry")
    assert entry.result["route"] == "log"
    assert "no arrival today" in entry.result["reason"]
    assert _step(run, "notify_operator").status == "skipped"
    assert engine.context.dark_nights("cedar-1a") == 0


def test_sunset_unsettled_lights_fail_safe_to_notify():
    vision = FixtureVision(staged={
        "fx-dim": {"scene": "nothing_notable", "people_count": 0,
                   "note": "A murky frame at the door."}})
    run = _sunset(_engine(), "f15", play=SunsetCheck(vision=vision),
                  frame_id="fx-dim")
    assert run.data["outcome"] == "notify_operator"
    assert _step(run, "classify_entry").result["route"] == "notify"
    assert _step(run, "notify_operator").status == "held"


def test_new_camera_plays_registered_but_never_message_triggered():
    assert get("guest_lockout") is not None
    assert get("guest_unsafe") is not None
    assert get("camera_sunset_check") is not None
    # Same proven-neutral message as the camera_check registration
    # test: none of the new plays starts from a triage Decision.
    assert match(Decision(Category.ACT, 0.9),
                 "There are people outside, the camera went off",
                 {}) is None


# ---------------------------------------------------------------------
# Batch 2: classification schema additions (#46 animal, #32 ground)
# ---------------------------------------------------------------------

def _held(run):
    return _step(run, "notify_operator").status == "held"

def test_schema_ground_parses_and_validates():
    ok = parse_classification(
        '{"scene": "nothing_notable", "ground": "snow_covered", '
        '"note": "x"}')
    assert ok.ground == "snow_covered"
    legacy = parse_classification(
        '{"scene": "nothing_notable", "note": "x"}')
    assert legacy.ground == "unknown"
    assert parse_classification(
        '{"scene": "nothing_notable", "ground": "snowy"}') is None
    assert parse_classification(
        '{"scene": "nothing_notable", "ground": null}') is None


def test_schema_animal_parses_and_validates():
    ok = parse_classification(
        '{"scene": "animal", "animal": "dog_sized", "note": "x"}')
    assert ok.animal == "dog_sized"
    legacy = parse_classification('{"scene": "animal", "note": "x"}')
    assert legacy.animal == "unknown"
    assert parse_classification(
        '{"scene": "animal", "animal": "big"}') is None
    assert parse_classification(
        '{"scene": "animal", "animal": null}') is None


# ---------------------------------------------------------------------
# Batch 2: dog with no pet on the booking (#46)
# ---------------------------------------------------------------------

def test_dog_arriving_with_party_flags_operator_with_exact_language():
    run = _event(_engine(), "f19")
    assert run.data["outcome"] == "notify_operator"
    cls = _step(run, "classify_frame")
    assert cls.result["animal"] == "dog_sized"
    assert cls.result["reason"] == DOG_REASON
    assert _held(run)
    notify = _step(run, "notify_operator")
    assert "a dog-sized animal was seen arriving with the party" \
        in notify.result["summary"]
    assert "the booking lists no pet" in notify.result["summary"]
    assert "Nothing has been charged" in notify.result["summary"]
    assert "service animals are exempt" in notify.result["summary"]
    assert "frame f19" in notify.result["summary"]
    # The only message path in Play 7 never fires on this case.
    assert all(s.action_key != "message_guest" for s in run.steps)


def test_dog_sized_animal_in_arrival_window_flags_operator():
    vision = FixtureVision({"fx-dog-yard": {
        "scene": "animal", "people_count": None, "animal": "dog_sized",
        "note": "A dog in the yard as the party arrives."}})
    run = _event(_engine(), "f19", ts="2026-10-28T16:05:00",
                 frame_id="fx-dog-yard",
                 play=CameraCheck(vision=vision))
    assert run.data["outcome"] == "notify_operator"
    assert _step(run, "classify_frame").result["reason"] == DOG_REASON


def test_dog_with_pet_on_booking_logs_only():
    data = default_fixture_data()
    data["arrivals"]["maple-house|2026-10-28"]["pet_on_booking"] = True
    run = _event(_engine(data=data), "f19")
    assert run.data["outcome"] == "logged"
    assert _step(run, "notify_operator").status == "skipped"
    assert _step(run, "log_event").result["logged"] is True


def test_lone_dog_at_night_logs_only():
    run = _event(_engine(), "f20")
    assert run.data["outcome"] == "logged"
    assert _step(run, "notify_operator").status == "skipped"
    assert _step(run, "classify_frame").result["route"] == "log"


def test_small_animal_with_arriving_party_logs_only():
    vision = FixtureVision({"fx-cat-party": {
        "scene": "animal", "people_count": None, "animal": "small",
        "note": "A cat slips past the arriving guests."}})
    run = _event(_engine(), "f19", ts="2026-10-28T16:02:00",
                 frame_id="fx-cat-party",
                 play=CameraCheck(vision=vision))
    assert run.data["outcome"] == "logged"


# ---------------------------------------------------------------------
# Batch 2: snow at an empty home — morning check (#32)
# ---------------------------------------------------------------------

def _morning(engine, frame_id, play=None, **overrides):
    rec = STAGED_FRAMES[frame_id]
    payload = {"property_id": rec["property_id"], "date": str(rec["date"]),
               "kind": "morning_check", "camera": rec["camera"],
               "ts": rec["ts"], "frame_id": frame_id}
    payload.update(overrides)
    return handle_morning_check_event(engine, payload, play=play)


def test_snow_at_empty_home_flags_shoveler_not_urgent():
    run = _morning(_engine(), "f21")
    assert run.play == "camera_morning_check"
    assert run.data["outcome"] == "notify_operator"
    cls = _step(run, "classify_ground")
    assert cls.result["ground"] == "snow_covered"
    assert cls.result["urgent"] is False
    assert _held(run)
    notify = _step(run, "notify_operator")
    assert "send the shoveler" in notify.result["summary"]
    assert notify.result["urgent"] is False
    assert all(s.action_key != "message_guest" for s in run.steps)


def test_snow_with_guest_and_turnover_due_is_urgent():
    run = _morning(_engine(), "f22")
    assert run.data["outcome"] == "notify_operator"
    cls = _step(run, "classify_ground")
    assert cls.result["ground"] == "snow_covered"
    assert cls.result["urgent"] is True
    notify = _step(run, "notify_operator")
    assert "a cleaner or guest is due at this home today" \
        in notify.result["summary"]


def test_clear_ground_logs_quietly():
    run = _morning(_engine(), "f23")
    assert run.data["outcome"] == "logged"
    assert _step(run, "notify_operator").status == "skipped"
    assert _step(run, "log_event").result["logged"] is True


def test_unsettled_ground_notifies_fail_safe():
    run = _morning(_engine(), "f24")
    assert run.data["outcome"] == "notify_operator"
    assert _step(run, "classify_ground").result["ground"] == "unknown"


def test_occupied_home_is_skipped_quietly():
    run = _morning(_engine(), "f25")
    assert run.data["outcome"] == "logged"
    cls = _step(run, "classify_ground")
    assert "stay is in progress" in cls.result["reason"]


def test_snow_before_midmorning_deadline_is_noted_not_flagged():
    vision = FixtureVision({"fx-early-snow": {
        "scene": "nothing_notable", "people_count": None,
        "ground": "snow_covered", "note": "Snow down at first light."}})
    run = handle_morning_check_event(
        _engine(), {"property_id": "maple-house", "date": "2026-10-29",
                    "kind": "morning_check", "camera": "front-door",
                    "ts": "2026-10-29T09:30:00",
                    "frame_id": "fx-early-snow"},
        play=MorningCheck(vision=vision))
    assert run.data["outcome"] == "logged"
    assert "before the mid-morning deadline" in \
        _step(run, "classify_ground").result["reason"]


def test_morning_check_malformed_event_rejected():
    with pytest.raises(ValueError):
        handle_morning_check_event(
            _engine(), {"property_id": "maple-house",
                        "date": "2026-10-29", "kind": "weekly",
                        "frame_id": "f21"})
    with pytest.raises(ValueError):
        handle_morning_check_event(
            _engine(), {"property_id": "maple-house",
                        "date": "2026-10-29", "kind": "morning_check"})


# ---------------------------------------------------------------------
# Batch 2: several cameras drop at once — area outage (#36)
# ---------------------------------------------------------------------

def _burst_events():
    return [
        {"property_id": "cedar-1a", "date": "2026-10-29",
         "camera": "front-door", "ts": "2026-10-29T19:20:00"},
        {"property_id": "cedar-2a", "date": "2026-10-29",
         "camera": "front-door", "ts": "2026-10-29T19:26:00"},
        {"property_id": "cedar-1a", "date": "2026-10-29",
         "camera": "backyard", "ts": "2026-10-29T19:31:00"},
    ]


def test_outage_below_threshold_changes_nothing():
    _reset_outage_correlator()
    out = handle_offline_burst(_engine(), _burst_events()[:2])
    assert out["cluster"] is None
    assert out["cluster_run"] is None
    assert len(out["runs"]) == 2
    for run in out["runs"]:
        assert all(s.name != "outage_cluster" for s in run.steps)


def test_outage_cluster_one_notification_and_marked_members():
    _reset_outage_correlator()
    out = handle_offline_burst(_engine(), _burst_events())
    cluster = out["cluster"]
    assert cluster is not None
    assert cluster["cameras"] == 3
    assert cluster["properties"] == ["cedar-1a", "cedar-2a"]
    assert cluster["span_minutes"] == 11
    cluster_run = out["cluster_run"]
    assert cluster_run is not None
    assert cluster_run.play == "camera_outage_cluster"
    assert cluster_run.data["outcome"] == "area_outage_notified"
    assert _held(cluster_run)
    notify = _step(cluster_run, "notify_operator")
    assert "Likely area power or internet outage" \
        in notify.result["summary"]
    assert "Garden Unit 1A" in notify.result["summary"]
    assert "Studio 2A" in notify.result["summary"]
    # Every member run is marked with the same cluster_id.
    for run in out["runs"]:
        mark = _step(run, "outage_cluster")
        assert mark is not None
        assert mark.result["cluster_id"] == cluster["cluster_id"]


def test_outage_cluster_refire_joins_quietly():
    _reset_outage_correlator()
    engine = _engine()
    first = handle_offline_burst(engine, _burst_events())
    assert first["cluster_run"] is not None
    second = handle_offline_burst(engine, _burst_events())
    assert second["cluster_run"] is None
    assert second["cluster"] is None
    for run in second["runs"]:
        mark = _step(run, "outage_cluster")
        assert mark is not None
        assert mark.result["cluster_id"] == first["cluster"]["cluster_id"]


def _maple_offline(ts="2026-10-29T19:34:00"):
    # A fourth camera, at a third Cedar Hollow property, inside the window.
    return {"property_id": "maple-house", "date": "2026-10-29",
            "camera": "front-door", "ts": ts}


def test_outage_fourth_camera_later_burst_merges_quietly():
    _reset_outage_correlator()
    engine = _engine()
    first = handle_offline_burst(engine, _burst_events())
    cid = first["cluster"]["cluster_id"]
    later = handle_offline_burst(engine, [_maple_offline()])
    # Same outage, grown footprint: no second notification.
    assert later["cluster_run"] is None and later["cluster_runs"] == []
    assert later["cluster"] is None
    mark = _step(later["runs"][0], "outage_cluster")
    assert mark.result["cluster_id"] == cid
    # One cluster on record, now carrying the fourth camera.
    assert list(_NOTIFIED_CLUSTERS) == [cid]
    assert len(_NOTIFIED_CLUSTERS[cid]["signature"]) == 4
    assert len(_NOTIFIED_CLUSTERS[cid]["members"]) == 4


def test_outage_fourth_camera_same_burst_one_notification():
    _reset_outage_correlator()
    out = handle_offline_burst(_engine(), _burst_events() + [_maple_offline()])
    assert len(out["cluster_runs"]) == 1
    cid = out["cluster"]["cluster_id"]
    # The fourth camera is recorded and marked under the announced
    # cluster, not as a second cluster nobody hears about.
    for run in out["runs"]:
        assert _step(run, "outage_cluster").result["cluster_id"] == cid
    assert list(_NOTIFIED_CLUSTERS) == [cid]
    assert len(_NOTIFIED_CLUSTERS[cid]["members"]) == 4


def test_outage_two_areas_in_one_burst_each_notified():
    _reset_outage_correlator()
    data = default_fixture_data()
    for pid in ("area-b-1", "area-b-2"):
        prop = dict(data["properties"]["cedar-1a"])
        prop.update(name=f"Fixture {pid}", area="Area B")
        data["properties"][pid] = prop
    area_b = [
        {"property_id": "area-b-1", "date": "2026-10-29",
         "camera": "front-door", "ts": "2026-10-29T19:22:00"},
        {"property_id": "area-b-2", "date": "2026-10-29",
         "camera": "front-door", "ts": "2026-10-29T19:24:00"},
        {"property_id": "area-b-1", "date": "2026-10-29",
         "camera": "backyard", "ts": "2026-10-29T19:28:00"},
    ]
    out = handle_offline_burst(_engine(data=data), _burst_events() + area_b)
    # Two areas down are two outages: each gets its one notification.
    assert len(out["cluster_runs"]) == 2
    assert sorted(c["area"] for c in out["clusters"]) \
        == ["Area B", "Cedar Hollow"]
    assert out["cluster_run"] is out["cluster_runs"][0]
    for cluster_run in out["cluster_runs"]:
        assert cluster_run.data["outcome"] == "area_outage_notified"
        assert _held(cluster_run)
    by_area = {c["area"]: c["cluster_id"] for c in out["clusters"]}
    assert len(set(by_area.values())) == 2
    for run in out["runs"]:
        area = "Area B" if run.property_id.startswith("area-b") \
            else "Cedar Hollow"
        assert _step(run, "outage_cluster").result["cluster_id"] \
            == by_area[area]


def test_outage_wide_window_forms_no_cluster():
    _reset_outage_correlator()
    events = [
        {"property_id": "cedar-1a", "date": "2026-10-29",
         "camera": "front-door", "ts": "2026-10-29T19:00:00"},
        {"property_id": "cedar-2a", "date": "2026-10-29",
         "camera": "front-door", "ts": "2026-10-29T19:40:00"},
        {"property_id": "cedar-1a", "date": "2026-10-29",
         "camera": "backyard", "ts": "2026-10-29T20:20:00"},
    ]
    out = handle_offline_burst(_engine(), events)
    assert out["cluster"] is None
    assert out["cluster_run"] is None


def test_outage_winter_vacant_home_freeze_risk():
    _reset_outage_correlator()
    events = [
        {"property_id": "cedar-1a", "date": "2027-01-15",
         "camera": "front-door", "ts": "2027-01-15T03:10:00"},
        {"property_id": "cedar-2a", "date": "2027-01-15",
         "camera": "front-door", "ts": "2027-01-15T03:16:00"},
        {"property_id": "cedar-1a", "date": "2027-01-15",
         "camera": "backyard", "ts": "2027-01-15T03:22:00"},
    ]
    out = handle_offline_burst(_engine(), events)
    assert out["cluster_run"] is not None
    notify = _step(out["cluster_run"], "notify_operator")
    assert notify.result["winter_freeze_risk"] is True
    assert "freeze risk" in notify.result["summary"]
    assert "outage at an empty home in winter is a freeze risk" \
        in notify.result["summary"]


# ---------------------------------------------------------------------
# Batch 2: camera upkeep (#58)
# ---------------------------------------------------------------------

def _upkeep(engine, data=None):
    return handle_camera_upkeep_event(
        engine, {"date": "2026-10-28", "kind": "camera_upkeep"})


def test_upkeep_report_notes_carry_their_evidence():
    run = _upkeep(_engine())
    assert run.play == "camera_upkeep"
    assert run.data["outcome"] == "upkeep_report"
    assert _held(run)
    build = _step(run, "build_report")
    notes = build.result["notes"]
    assert len(notes) == 3
    by_task = {n["task"]: n for n in notes}
    wifi = by_task["check the WiFi at Garden Unit 1A, Cedar Hollow"]
    assert "3 days" in wifi["evidence"]
    lens_2a = by_task["wipe the lens at Studio 2A, Cedar Hollow"]
    assert "41 of 46" in lens_2a["evidence"]
    lens_maple = by_task["wipe the lens at Maple Street House"]
    assert "18 of 30" in lens_maple["evidence"]
    notify = _step(run, "notify_operator")
    assert "nothing was sent to anyone" in notify.result["summary"]
    assert all(s.action_key != "message_guest" for s in run.steps)
    assert all(s.action_key != "message_cleaner" for s in run.steps)


def test_upkeep_below_thresholds_logs_without_notes():
    data = default_fixture_data()
    data["camera_history"] = [{
        "property_id": "cedar-1a", "camera": "front-door",
        "offline_events": [{"date": "2026-10-25", "time": "07:03"},
                           {"date": "2026-10-26", "time": "07:08"}],
        "night_motion": 5, "night_trivial": 2,
        "unclear_frames": 1, "frames_total": 30,
    }]
    run = _upkeep(_engine(data=data))
    assert run.data["outcome"] == "logged"
    assert _step(run, "build_report").result["notes"] == []
    assert _step(run, "notify_operator").status == "skipped"
    assert _step(run, "log_event").result["logged"] is True


def test_upkeep_no_history_reports_nothing_was_checked():
    # An empty fleet history is not a clean bill of health: the
    # operator is told nothing was checked.
    data = default_fixture_data()
    data["camera_history"] = []
    run = _upkeep(_engine(data=data))
    assert run.data["outcome"] == "upkeep_report"
    build = _step(run, "build_report")
    assert build.result["cameras_checked"] == 0
    assert build.result["notes"] == []
    assert _step(run, "log_event").status == "skipped"
    assert _held(run)
    summary = _step(run, "notify_operator").result["summary"]
    assert "no camera history on record — nothing was checked" in summary
    assert "not a clean bill of health" in summary
    assert "Nothing was sent to anyone" in summary
    assert all(s.action_key != "message_guest" for s in run.steps)
    assert all(s.action_key != "message_cleaner" for s in run.steps)


def test_upkeep_malformed_event_rejected():
    with pytest.raises(ValueError):
        handle_camera_upkeep_event(_engine(), {})
    with pytest.raises(ValueError):
        handle_camera_upkeep_event(
            _engine(), {"date": "2026-10-28", "kind": "weekly"})


def test_batch2_plays_registered_and_neutral_message_matches_none():
    from jev.plays import get, match
    assert get("camera_morning_check") is not None
    assert get("camera_upkeep") is not None
    assert get("camera_outage_cluster") is not None
    decision = Decision(Category.CONFIRM, 0.9)
    assert match(decision, "There are people outside, the camera "
                           "went off", {}) is None


# ---------------------------------------------------------------------
# Batch 3: classification schema additions (#55 carrying, #23 worker)
# ---------------------------------------------------------------------

def test_schema_carrying_and_worker_parse_and_validate():
    ok = parse_classification(
        '{"scene": "expected_guest", "note": "x", '
        '"carrying": "boxes_or_belongings", "worker": "present"}')
    assert ok.carrying == "boxes_or_belongings" and ok.worker == "present"
    legacy = parse_classification('{"scene": "expected_guest", "note": "x"}')
    assert legacy.carrying == "unknown" and legacy.worker == "unknown"
    assert parse_classification(
        '{"scene": "expected_guest", "carrying": "boxes"}') is None
    assert parse_classification(
        '{"scene": "expected_guest", "carrying": null}') is None
    assert parse_classification(
        '{"scene": "cleaner", "worker": "yes"}') is None
    assert parse_classification(
        '{"scene": "cleaner", "worker": null}') is None


# ---------------------------------------------------------------------
# Batch 3: gathering accumulation (#6)
# ---------------------------------------------------------------------

def _gathering_world(booked):
    # Alex's stay at Garden Unit 1A on 2026-10-29, with the booked
    # party set to `booked`.
    data = default_fixture_data()
    data["stays"]["cedar-1a|2026-10-29"]["guest_count"] = booked
    return data


def _run_frames(engine, frames, scene="large_gathering",
                property_id="cedar-1a", camera="backyard", **extra):
    # frames: [(frame_id, iso_ts, people_estimate)]. Each is staged
    # for a custom FixtureVision and delivered as one motion event,
    # in order. Returns the runs.
    staged = {fid: {"scene": scene, "people_count": est,
                    "note": "People on the patio.", **extra}
              for fid, _ts, est in frames}
    play = CameraCheck(vision=FixtureVision(staged=staged))
    return [handle_camera_event(engine, {
        "property_id": property_id, "date": ts[:10], "camera": camera,
        "ts": ts, "frame_id": fid}, play=play) for fid, ts, _est in frames]


def _evening(levels, times=("21:00", "21:10", "21:20"), prefix="g"):
    return [(f"{prefix}{i}", f"2026-10-29T{t}:00", n)
            for i, (n, t) in enumerate(zip(levels, times))]


def test_gathering_single_frame_logs_even_far_above_booked():
    _reset_gathering_frames()
    run = _event(_engine(), "f3")
    # Eleven people (model estimate) against a booked four: one frame
    # is one guess, and it logs only.
    assert run.data["outcome"] == "logged"
    assert run.data["gathering_case"] is False
    classify = _step(run, "classify_frame")
    assert "a single frame never fires" in classify.result["reason"]
    assert classify.result["gathering"]["floor"] is None
    assert _step(run, "notify_operator").status == "skipped"
    assert _step(run, "log_event").result["logged"] is True


def test_gathering_fires_at_booked_plus_margin_never_below():
    # The owner's rule, restated here independently of the code:
    # margin = max(4, ceil(booked / 2)). Sustained estimates at
    # booked, booked + 1, and booked + margin - 1 never fire;
    # booked + margin fires.
    for booked in (1, 2, 4, 7, 8, 10, 13):
        margin = max(4, math.ceil(booked / 2))
        assert gathering_margin(booked) == margin, booked
        for level, fires in ((booked, False), (booked + 1, False),
                             (booked + margin - 1, False),
                             (booked + margin, True)):
            _reset_gathering_frames()
            runs = _run_frames(_engine(data=_gathering_world(booked)),
                               _evening([level] * 3))
            last = runs[-1]
            assert (last.data["outcome"] == "notify_operator") is fires, \
                (booked, level)
            assert last.data["gathering_case"] is fires, (booked, level)
            assert all(r.data["outcome"] == "logged" for r in runs[:-1])
            read = _step(last, "classify_frame").result["gathering"]
            assert read["threshold"] == booked + margin
            assert read["floor"] == level
            if fires:
                notify = _step(last, "notify_operator")
                assert notify.status == "held"
                assert notify.result["executed"] is False
                assert f"at least {level} people (model estimate" \
                    in notify.result["summary"]
            else:
                assert _step(last, "notify_operator").status == "skipped"


def test_gathering_one_peak_frame_cannot_carry_the_floor():
    _reset_gathering_frames()
    runs = _run_frames(_engine(), _evening([4, 30, 4]))
    assert all(r.data["outcome"] == "logged" for r in runs)
    read = _step(runs[-1], "classify_frame").result["gathering"]
    # The peak is one frame; what was sustained is four.
    assert read["floor"] == 4 and read["threshold"] == 8
    assert read["fire"] is False


def test_gathering_needs_enough_frames_and_enough_span():
    _reset_gathering_frames()
    two = _run_frames(_engine(), _evening([12, 12], ("21:00", "21:30")))
    assert all(r.data["outcome"] == "logged" for r in two)
    assert _step(two[-1], "classify_frame").result["gathering"]["floor"] \
        is None
    # Three frames inside five minutes are one moment, not a
    # sustained gathering; a fourth frame twenty-five minutes on
    # makes it sustained.
    _reset_gathering_frames()
    engine = _engine()
    burst = _run_frames(engine, _evening([12, 12, 12],
                                         ("21:00", "21:02", "21:05")))
    assert all(r.data["outcome"] == "logged" for r in burst)
    later = _run_frames(engine, [("g9", "2026-10-29T21:25:00", 12)])[0]
    assert later.data["outcome"] == "notify_operator"
    assert _step(later, "classify_frame").result["gathering"][
        "span_minutes"] == 25


def test_gathering_window_rolls():
    _reset_gathering_frames()
    runs = _run_frames(_engine(), _evening(
        [12, 12, 12, 12], ("21:00", "22:00", "23:00", "23:10")))
    # At 11 PM the 9 PM frame has rolled out of the 90-minute
    # window: two frames, nothing sustained. At 11:10 PM three
    # frames inside the window span 70 minutes: it fires.
    assert [r.data["outcome"] for r in runs] == \
        ["logged", "logged", "logged", "notify_operator"]
    read = _step(runs[-1], "classify_frame").result["gathering"]
    assert [f["frame_id"] for f in read["frames"]] == ["g1", "g2", "g3"]


def test_gathering_accumulates_per_stay_never_across_bookings():
    _reset_gathering_frames()
    engine = _engine()
    # Alex's stay late on the 29th, then Sam's stay just after
    # midnight: inside one 90-minute window, but two bookings.
    alex = _run_frames(engine, [("a0", "2026-10-29T23:30:00", 12),
                                ("a1", "2026-10-29T23:45:00", 12)])
    sam = _run_frames(engine, [("s0", "2026-10-30T00:05:00", 12)])
    assert all(r.data["outcome"] == "logged" for r in alex + sam)
    more = _run_frames(engine, [("s1", "2026-10-30T00:20:00", 12),
                                ("s2", "2026-10-30T00:30:00", 12)])
    assert more[-1].data["outcome"] == "notify_operator"
    read = _step(more[-1], "classify_frame").result["gathering"]
    assert [f["frame_id"] for f in read["frames"]] == ["s0", "s1", "s2"]
    assert read["booked"] == 2


def test_gathering_party_scene_frames_accumulate_unknown_people_do_not():
    _reset_gathering_frames()
    # Dana's arrival day at Studio 2A, booked party of two: arrival
    # frames reading six people sustain a floor of six = 2 + 4.
    guests = _run_frames(_engine(), [
        ("e0", "2026-10-28T15:00:00", 6), ("e1", "2026-10-28T15:10:00", 6),
        ("e2", "2026-10-28T15:20:00", 6)],
        scene="expected_guest", property_id="cedar-2a", camera="front-door")
    assert guests[-1].data["gathering_case"] is True
    _reset_gathering_frames()
    strangers = _run_frames(_engine(), [
        ("u0", "2026-10-28T15:00:00", 6), ("u1", "2026-10-28T15:10:00", 6),
        ("u2", "2026-10-28T15:20:00", 6)],
        scene="unknown_person", property_id="cedar-2a", camera="front-door")
    # Unknown people notify on their own reason, frame by frame;
    # they never build a gathering claim.
    for run in strangers:
        assert run.data["outcome"] == "notify_operator"
        assert run.data["gathering"] is None
        assert run.data["gathering_case"] is False


def test_gathering_notifies_once_then_again_only_when_it_grows():
    _reset_gathering_frames()
    engine = _engine()
    first = _run_frames(engine, _evening([9, 9, 9]))
    assert first[-1].data["outcome"] == "notify_operator"
    # Still sustained at nine: already flagged, logged, not re-sent.
    again = _run_frames(engine, [("g3", "2026-10-29T21:30:00", 9)])[0]
    assert again.data["outcome"] == "logged"
    assert "already flagged" in _step(again, "classify_frame").result["reason"]
    # A re-fired frame is one frame, not two.
    refire = _run_frames(engine, [("g3", "2026-10-29T21:30:00", 9)])[0]
    assert _step(refire, "classify_frame").result["gathering"][
        "frames_in_window"] == 4
    # Grown by another full margin (9 + 4 = 13): notified again.
    grown = _run_frames(engine, [("h0", "2026-10-29T21:40:00", 13),
                                 ("h1", "2026-10-29T21:50:00", 13),
                                 ("h2", "2026-10-29T22:00:00", 13)])
    assert [r.data["outcome"] for r in grown] == \
        ["logged", "logged", "notify_operator"]
    assert _step(grown[-1], "notify_operator").result["at_least"] == 13


def test_gathering_frame_with_no_estimate_fails_safe_without_a_claim():
    _reset_gathering_frames()
    vision = FixtureVision({"fx-crowd": {
        "scene": "large_gathering", "people_count": None,
        "note": "A crowd on the patio."}})
    run = _event(_engine(), "f3", play=CameraCheck(vision=vision),
                 frame_id="fx-crowd")
    assert run.data["outcome"] == "notify_operator"
    assert _held(run)
    reason = _step(run, "classify_frame").result["reason"]
    assert "carries no people estimate" in reason
    assert "could not be checked against the booked party" in reason
    summary = _step(run, "notify_operator").result["summary"]
    assert "more people" not in summary and "at least" not in summary
    assert run.data["gathering"] is None


def test_gathering_unreadable_frames_never_count_but_still_notify():
    _reset_gathering_frames()
    engine = _engine()
    # A covered camera's frames notify for the camera, and their
    # people reads are worthless — they never accumulate.
    covered = _run_frames(engine, _evening([20, 20, 20], prefix="c"),
                          view="blocked")
    for run in covered:
        assert run.data["outcome"] == "notify_operator"
        assert "view reads blocked" in run.data["flag_reason"]
        assert run.data["gathering"] is None
    # A classify failure in the middle of a run notifies on its own
    # and is not counted; the run needs a third readable frame.
    _reset_gathering_frames()
    staged = {"k0": {"scene": "large_gathering", "people_count": 12,
                     "note": "x"},
              "k2": {"scene": "large_gathering", "people_count": 12,
                     "note": "x"},
              "k3": {"scene": "large_gathering", "people_count": 12,
                     "note": "x"}}
    play = CameraCheck(vision=FixtureVision(staged=staged))
    engine = _engine()
    outcomes = []
    for fid, t in (("k0", "21:00"), ("k1-unstaged", "21:10"),
                   ("k2", "21:20"), ("k3", "21:30")):
        run = handle_camera_event(engine, {
            "property_id": "cedar-1a", "date": "2026-10-29",
            "camera": "backyard", "ts": f"2026-10-29T{t}:00",
            "frame_id": fid}, play=play)
        outcomes.append((run.data["outcome"],
                         _step(run, "classify_frame").result["scene"]))
    assert outcomes == [("logged", "large_gathering"),
                        ("notify_operator", "unclassified"),
                        ("logged", "large_gathering"),
                        ("notify_operator", "large_gathering")]


def test_gathering_at_a_closed_house_or_with_no_booking_still_notifies():
    _reset_gathering_frames()
    closed = _run_frames(_engine(), [("v0", "2026-10-30T21:00:00", 5)],
                         property_id="maple-house", camera="front-door")[0]
    assert closed.data["outcome"] == "notify_operator"
    assert "closed and should be vacant" in closed.data["flag_reason"]
    assert closed.data["gathering"] is None
    unbooked = _run_frames(_engine(), [("n0", "2026-10-28T21:00:00", 5)])[0]
    assert unbooked.data["outcome"] == "notify_operator"
    assert "no arrival or stay on record" in unbooked.data["flag_reason"]


def test_gathering_span_edge_nineteen_minutes_logs_twenty_fires():
    _reset_gathering_frames()
    short = _run_frames(_engine(), _evening([12] * 3,
                                            ("21:00", "21:10", "21:19")))
    assert all(r.data["outcome"] == "logged" for r in short)
    assert _step(short[-1], "classify_frame").result["gathering"][
        "floor"] is None
    _reset_gathering_frames()
    edge = _run_frames(_engine(), _evening([12] * 3))   # 21:00 to 21:20
    assert [r.data["outcome"] for r in edge] == \
        ["logged", "logged", "notify_operator"]
    assert _step(edge[-1], "classify_frame").result["gathering"][
        "span_minutes"] == 20


def test_gathering_window_edge_ninety_minutes_stays_ninety_one_drops():
    _reset_gathering_frames()
    inside = _run_frames(_engine(), _evening([12] * 3,
                                             ("21:00", "21:45", "22:30")))
    # A frame exactly 90 minutes before the newest is still in.
    assert inside[-1].data["outcome"] == "notify_operator"
    read = _step(inside[-1], "classify_frame").result["gathering"]
    assert read["frames_in_window"] == 3 and read["span_minutes"] == 90
    _reset_gathering_frames()
    outside = _run_frames(_engine(), _evening([12] * 3,
                                              ("21:00", "21:45", "22:31")))
    # At 91 minutes it has rolled out: two frames, nothing sustained.
    assert all(r.data["outcome"] == "logged" for r in outside)
    read = _step(outside[-1], "classify_frame").result["gathering"]
    assert read["frames_in_window"] == 2 and read["floor"] is None


def test_gathering_two_cameras_never_sum_and_one_minute_is_one_moment():
    def both_cameras(engine, times, est, prefix):
        runs = []
        for i, t in enumerate(times):
            for cam in ("backyard", "front-door"):
                runs += _run_frames(engine, [(f"{prefix}-{cam}-{i}",
                                              f"2026-10-29T{t}:00", est)],
                                    camera=cam)
        return runs

    _reset_gathering_frames()
    # Alex's booked four, threshold eight. Both cameras read five at
    # the same three minutes: summed that would be ten; never summed,
    # the floor is five and nothing fires.
    runs = both_cameras(_engine(), ("21:00", "21:10", "21:20"), 5, "s")
    assert all(r.data["outcome"] == "logged" for r in runs)
    read = _step(runs[-1], "classify_frame").result["gathering"]
    assert read["frames_in_window"] == 6
    assert read["floor"] == 5 and read["threshold"] == 8
    # Two cameras at one minute are one moment: four frames at two
    # minutes, twenty apart, do not sustain; a third minute does.
    _reset_gathering_frames()
    engine = _engine()
    runs = both_cameras(engine, ("21:00", "21:20"), 9, "m")
    assert all(r.data["outcome"] == "logged" for r in runs)
    assert _step(runs[-1], "classify_frame").result["gathering"][
        "floor"] is None
    third = _run_frames(engine, [("m-backyard-2", "2026-10-29T21:30:00", 9)])[0]
    assert third.data["outcome"] == "notify_operator"
    assert "sustained across 5 frames at 3 moments over 30 minutes" \
        in _step(third, "notify_operator").result["summary"]


# ---------------------------------------------------------------------
# Batch 3: move-in signs (#55)
# ---------------------------------------------------------------------

_MOVE_IN_ASSERTIONS = ("is moving in", "are moving in", "moved in",
                       "move-in is happening", "is a move-in")


def test_move_in_three_distinct_days_raise_one_awareness_notification():
    _reset_move_in_signs()
    engine = _engine()
    luggage = _event(engine, "f29")
    # Arrival-day suitcases are ordinary luggage: never counted.
    assert luggage.data["outcome"] == "logged"
    assert luggage.data["move_in"] is None
    day1, day2 = _event(engine, "f30"), _event(engine, "f31")
    for run, n in ((day1, 1), (day2, 2)):
        assert run.data["outcome"] == "logged"
        assert run.data["move_in"]["days"] == n
        assert f"{n} of {MOVE_IN_MIN_DAYS} distinct days" \
            in _step(run, "classify_frame").result["reason"]
        assert _step(run, "notify_operator").status == "skipped"
    day3 = _event(engine, "f32")
    assert day3.data["outcome"] == "notify_operator"
    assert day3.data["move_in_case"] is True
    notify = _step(day3, "notify_operator")
    assert notify.status == "held" and notify.result["executed"] is False
    assert notify.result["awareness_only"] is True
    summary = notify.result["summary"]
    assert "signs consistent with a possible move-in" in summary
    assert "Awareness only: these signs do not confirm a move-in" in summary
    assert "f30 (2026-11-03), f31 (2026-11-04), f32 (2026-11-05)" in summary
    assert "Nothing was sent to the guest" in summary
    for claim in _MOVE_IN_ASSERTIONS:
        assert claim not in summary.lower(), claim
    assert all(s.action_key != "message_guest" for s in day3.steps)
    # ONE notification per stay: later frames log, noting it.
    after = _event(engine, "f33")
    assert after.data["outcome"] == "logged"
    assert "already flagged" in _step(after, "classify_frame").result["reason"]


def _boxes(engine, frames, carrying="boxes_or_belongings",
           scene="expected_guest"):
    # frames: [(frame_id, iso_ts)] at Studio 2A's front door.
    staged = {fid: {"scene": scene, "people_count": 1,
                    "note": "A person carrying things to the door.",
                    "carrying": carrying} for fid, _ts in frames}
    play = CameraCheck(vision=FixtureVision(staged=staged))
    return [handle_camera_event(engine, {
        "property_id": "cedar-2a", "date": ts[:10], "camera": "front-door",
        "ts": ts, "frame_id": fid}, play=play) for fid, ts in frames]


def test_move_in_below_the_pattern_logs_only():
    _reset_move_in_signs()
    two_days = _boxes(_engine(), [("b0", "2026-11-03T18:00:00"),
                                  ("b1", "2026-11-04T18:00:00")])
    assert all(r.data["outcome"] == "logged" for r in two_days)
    _reset_move_in_signs()
    # Three frames, one day: one distinct day, not a pattern.
    one_day = _boxes(_engine(), [("d0", "2026-11-03T17:00:00"),
                                 ("d1", "2026-11-03T18:00:00"),
                                 ("d2", "2026-11-03T19:00:00")])
    assert all(r.data["outcome"] == "logged" for r in one_day)
    assert one_day[-1].data["move_in"]["days"] == 1


def test_move_in_luggage_and_unread_carrying_never_count():
    days = [("x0", "2026-11-03T18:00:00"), ("x1", "2026-11-04T18:00:00"),
            ("x2", "2026-11-05T18:00:00")]
    for carrying in ("luggage", "none", "unknown"):
        _reset_move_in_signs()
        runs = _boxes(_engine(), days, carrying=carrying)
        assert all(r.data["outcome"] == "logged" for r in runs), carrying
        assert all(r.data["move_in"] is None for r in runs), carrying


def test_move_in_pattern_is_per_stay():
    _reset_move_in_signs()
    data = default_fixture_data()
    data["stays"]["cedar-2a|2026-11-06"] = {
        "guest_name": "Riley", "guest_count": 1,
        "arrival_date": "2026-11-06", "departure_date": "2026-11-09"}
    runs = _boxes(_engine(data=data), [("p0", "2026-11-03T18:00:00"),
                                       ("p1", "2026-11-04T18:00:00"),
                                       ("p2", "2026-11-06T18:00:00")])
    # Two days on Jordan's stay and one on Riley's: no pattern.
    assert all(r.data["outcome"] == "logged" for r in runs)
    assert runs[-1].data["move_in"]["days"] == 1


def test_move_in_rides_along_with_a_frame_that_already_notifies():
    _reset_move_in_signs()
    engine = _engine()
    _boxes(engine, [("q0", "2026-11-03T18:00:00"),
                    ("q1", "2026-11-04T18:00:00")])
    third = _boxes(engine, [("q2", "2026-11-05T18:00:00")],
                   scene="unknown_person")[0]
    # The frame's own reason and the pattern both reach the
    # operator; neither silences the other.
    assert third.data["outcome"] == "notify_operator"
    assert third.data["move_in_case"] is True
    reason = third.data["flag_reason"]
    assert "matches nobody expected" in reason
    assert "signs consistent with a possible move-in" in reason


def test_move_in_vision_failure_fails_safe_and_never_counts():
    _reset_move_in_signs()
    engine = _engine()
    _boxes(engine, [("r0", "2026-11-03T18:00:00"),
                    ("r1", "2026-11-04T18:00:00")])
    failed = handle_camera_event(engine, {
        "property_id": "cedar-2a", "date": "2026-11-05",
        "camera": "front-door", "ts": "2026-11-05T18:00:00",
        "frame_id": "r2"}, play=CameraCheck(vision=_BoomVision()))
    assert failed.data["outcome"] == "notify_operator"
    assert failed.data["move_in_case"] is False
    assert "could not be classified" in failed.data["flag_reason"]
    assert "move-in" not in failed.data["flag_reason"]


def test_move_in_with_no_arrival_date_on_record_says_so_on_the_log():
    _reset_move_in_signs()
    data = default_fixture_data()
    for day in ("2026-11-03", "2026-11-04", "2026-11-05"):
        data["stays"][f"cedar-2a|{day}"]["arrival_date"] = ""
    engine = _engine(data=data)
    # Each day keys on its own, so the pattern cannot complete — and
    # the log says why instead of going quiet.
    for run in (_event(engine, fid) for fid in ("f30", "f31", "f32")):
        assert run.data["outcome"] == "logged"
        assert run.data["move_in"]["days"] == 1
        assert ("the stay's arrival date is not on record, so days "
                "cannot be grouped across this stay") \
            in _step(run, "classify_frame").result["reason"]


# ---------------------------------------------------------------------
# Batch 3: vendor proof of service (#23)
# ---------------------------------------------------------------------

def _vendor(engine, play=None, close=True, close_payload=None, **payload):
    base = {"property_id": "maple-house", "date": "2026-10-30",
            "vendor": "Fixture Handyman",
            "window_start": "10:00", "window_end": "12:00"}
    base.update(payload)
    run = handle_vendor_window_event(engine, base, play=play)
    if close:
        handle_vendor_window_closed_event(engine, run, close_payload)
    return run


def test_vendor_proof_waits_for_the_stated_window_to_close():
    run = _vendor(_engine(), close=False)
    assert run.play == "camera_vendor_proof"
    assert run.status == "waiting"
    assert _step(run, "wait_vendor_window").status == "done"
    assert _step(run, "match_reality").result["window"] \
        == "10:00 AM–12:00 PM"
    assert _step(run, "wait_window_closed").status == "waiting"
    assert _step(run, "log_proof").status == "pending"
    assert _step(run, "notify_operator").status == "pending"


def test_vendor_sighting_writes_proof_of_service_log_with_frame():
    run = _vendor(_engine())
    assert run.status == "completed"
    assert run.data["outcome"] == "proof_of_service_logged"
    check = _step(run, "check_window_frames")
    # f6 (10:30 AM) is inside the window; f5 (10:15 PM) is not.
    assert [f["frame_id"] for f in check.result["window_frames"]] == ["f6"]
    assert check.result["sightings"] == ["f6"]
    proof = _step(run, "log_proof")
    assert proof.action_key == "auto_close" and proof.stake == "low"
    assert proof.result["logged"] is True
    assert proof.result["frame_id"] == "f6"
    assert proof.result["proof_of_service"][0]["frame_id"] == "f6"
    entry = proof.result["entry"]
    assert "frame f6" in entry and "10:30 AM" in entry
    assert "inside the stated arrival window" in entry
    assert "Presence evidence only" in entry
    assert "says nothing about who the person was" in entry
    # Never a word about the work itself, never an identity.
    for claim in ("completed the", "work is complete", "work was done",
                  "job done", "fixed", "Fixture Handyman was seen",
                  "the vendor was seen"):
        assert claim not in entry, claim
    assert _step(run, "notify_operator").status == "skipped"
    assert all(s.action_key not in ("message_guest", "message_cleaner",
                                    "dispatch_vendor") for s in run.steps)


def test_vendor_no_sighting_notifies_operator_held():
    run = _vendor(_engine(), property_id="cedar-1a", date="2026-10-29",
                  window_start="15:00", window_end="16:00")
    assert run.status == "completed"
    assert run.data["outcome"] == "notify_operator"
    check = _step(run, "check_window_frames")
    assert check.result["frames_checked"] == 1
    assert check.result["sightings"] == []
    notify = _step(run, "notify_operator")
    assert notify.action_key == "notify_operator"
    assert notify.status == "held" and notify.result["executed"] is False
    summary = notify.result["summary"]
    assert "no vendor was seen inside the stated window" in summary
    assert "3:00 PM–4:00 PM" in summary
    assert "f28 (front-door, 3:25 PM): nothing notable" in summary
    assert "not proof the visit did not happen" in summary
    assert "Nothing was sent to the vendor or the guest" in summary
    assert _step(run, "log_proof").status == "skipped"


def test_vendor_window_with_no_frames_says_so_plainly():
    run = _vendor(_engine(), window_start="13:00", window_end="14:00")
    assert run.data["outcome"] == "notify_operator"
    summary = _step(run, "notify_operator").result["summary"]
    assert "No camera frames are on record inside the window" in summary


def test_vendor_frames_outside_the_window_day_or_camera_are_ignored():
    frames = [{"frame_id": "w-early", "camera": "front-door",
               "ts": "2026-10-30T09:59:00"},
              {"frame_id": "w-late", "camera": "front-door",
               "ts": "2026-10-30T12:01:00"},
              {"frame_id": "w-day", "camera": "front-door",
               "ts": "2026-10-31T10:30:00"},
              {"frame_id": "w-cam", "camera": "driveway",
               "ts": "2026-10-30T10:30:00"}]
    vision = FixtureVision({fid: {"scene": "cleaner", "people_count": 1,
                                  "note": "x", "worker": "present"}
                            for fid in ("w-early", "w-late", "w-day",
                                        "w-cam")})
    run = _vendor(_engine(), play=VendorProof(vision=vision),
                  camera="front-door", close_payload={"frames": frames})
    assert run.data["outcome"] == "notify_operator"
    assert _step(run, "check_window_frames").result["frames_checked"] == 0


def test_vendor_unread_frames_are_never_sightings():
    frames = [{"frame_id": fid, "camera": "front-door",
               "ts": f"2026-10-30T{t}:00"}
              for fid, t in (("u-dark", "10:20"), ("u-covered", "10:40"),
                             ("u-unstaged", "11:00"))]
    vision = FixtureVision({
        "u-dark": {"scene": "unclear", "people_count": None,
                   "note": "Too dark.", "worker": "present"},
        "u-covered": {"scene": "cleaner", "people_count": 1, "note": "x",
                      "view": "blocked", "worker": "present"}})
    run = _vendor(_engine(), play=VendorProof(vision=vision),
                  close_payload={"frames": frames})
    assert run.data["outcome"] == "notify_operator"
    check = _step(run, "check_window_frames")
    assert check.result["unreadable"] == 3
    assert check.result["sightings"] == []
    summary = _step(run, "notify_operator").result["summary"]
    assert "could not be read" in summary
    assert "3 of them could not be read" in summary


def test_vendor_person_not_reading_as_worker_is_not_a_sighting():
    frames = [{"frame_id": "n-person", "camera": "front-door",
               "ts": "2026-10-30T10:20:00"},
              {"frame_id": "n-absent", "camera": "front-door",
               "ts": "2026-10-30T10:40:00"}]
    vision = FixtureVision({
        "n-person": {"scene": "unknown_person", "people_count": 1,
                     "note": "A person at the door."},
        # An explicit "absent" is never overridden by the scene.
        "n-absent": {"scene": "cleaner", "people_count": 1, "note": "x",
                     "worker": "absent"}})
    run = _vendor(_engine(), play=VendorProof(vision=vision),
                  close_payload={"frames": frames})
    assert run.data["outcome"] == "notify_operator"
    summary = _step(run, "notify_operator").result["summary"]
    assert "n-person (front-door, 10:20 AM): unknown person — a person " \
           "who did not read as a worker; check the still" in summary


def test_vendor_unknown_worker_read_never_writes_proof_of_service():
    frames = [{"frame_id": "k-missing", "camera": "front-door",
               "ts": "2026-10-30T10:20:00"},
              {"frame_id": "k-unknown", "camera": "front-door",
               "ts": "2026-10-30T10:40:00"}]
    # A cleaner scene with the worker field unread (missing, or an
    # explicit "unknown") is never presence evidence.
    vision = FixtureVision({
        "k-missing": {"scene": "cleaner", "people_count": 1, "note": "x"},
        "k-unknown": {"scene": "cleaner", "people_count": 1, "note": "x",
                      "worker": "unknown"}})
    run = _vendor(_engine(), play=VendorProof(vision=vision),
                  close_payload={"frames": frames})
    assert run.data["outcome"] == "notify_operator"
    check = _step(run, "check_window_frames")
    assert check.result["frames_checked"] == 2
    assert check.result["sightings"] == []
    assert _step(run, "log_proof").status == "skipped"
    assert _held(run)
    summary = _step(run, "notify_operator").result["summary"]
    assert "k-missing (front-door, 10:20 AM): cleaner — a person who " \
           "did not read as a worker; check the still" in summary


def test_vendor_sighting_alongside_unread_frames_still_logs_proof():
    frames = [{"frame_id": "s-dark", "camera": "front-door",
               "ts": "2026-10-30T10:10:00"},
              {"frame_id": "s-worker", "camera": "front-door",
               "ts": "2026-10-30T10:45:00"}]
    vision = FixtureVision({
        "s-dark": {"scene": "unclear", "people_count": None, "note": "x"},
        "s-worker": {"scene": "unknown_person", "people_count": 1,
                     "note": "A person with a toolbox.",
                     "worker": "present"}})
    run = _vendor(_engine(), play=VendorProof(vision=vision),
                  close_payload={"frames": frames})
    assert run.data["outcome"] == "proof_of_service_logged"
    entry = _step(run, "log_proof").result["entry"]
    assert "frame s-worker" in entry
    assert "1 other frame(s) inside the window could not be read" in entry
    assert _step(run, "notify_operator").status == "skipped"


def test_vendor_proof_malformed_and_unknown_property():
    for bad in ({"window_start": ""}, {"window_end": "noon"},
                {"window_start": "10am"}, {"window_start": "25:00"},
                {"window_start": "12:00", "window_end": "10:00"},
                {"date": "Oct 30"}, {"property_id": ""}):
        with pytest.raises(ValueError):
            _vendor(_engine(), close=False, **bad)
    run = _vendor(_engine(), property_id="no-such-property")
    assert run.status == "held_for_human"
    assert _step(run, "notify_operator").status == "pending"


def test_batch3_plays_registered_and_neutral_message_matches_none():
    # Same proven-neutral message as the earlier registration
    # tests: the vendor proof play never starts from a Decision.
    assert get("camera_vendor_proof") is not None
    assert match(Decision(Category.ACT, 0.9),
                 "There are people outside, the camera went off",
                 {}) is None
