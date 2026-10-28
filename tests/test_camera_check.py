import json, os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
os.environ["JEV_SPEND_STATE"] = os.path.join(os.path.dirname(__file__), "spend-plays-test.json")

from jev.plays import (FixtureContext, PlayEngine, STAKES,
                       default_fixture_data, get, match)
from jev.plays.camera_check import CameraCheck, handle_camera_event
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
                     property_id="lockport-1n", date="2026-10-28",
                     frame_id=frame_id)
        assert run.status == "completed" and run.data["outcome"] == "logged"
        assert _step(run, "log_event").result["logged"] is True
        assert _step(run, "notify_operator").status == "skipped"


def test_large_gathering_notifies_operator_held_at_p1(tmp_path):
    log = str(tmp_path / "runs.jsonl")
    run = _event(_engine(log_path=log), "f3")
    assert run.status == "completed"
    assert run.data["outcome"] == "notify_operator"
    reality = _step(run, "match_reality")
    assert reality.result["mid_stay"] is True
    assert reality.result["expected_party"] == 4
    classify = _step(run, "classify_frame")
    assert classify.result["scene"] == "large_gathering"
    assert classify.result["people_count"] == 11
    assert classify.result["route"] == "notify"

    notify = _step(run, "notify_operator")
    assert notify.action_key == "notify_operator"
    assert notify.stake == "middle"
    summary = notify.result["summary"]
    assert "Owner's Unit 1N" in summary
    assert "11 people" in summary
    assert "party of 4" in summary
    assert "Nothing was sent to the guest" in summary
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
    ledger = {"categories": {"notify_operator": {
        "decisive_n": 30, "decisive_accuracy": 0.96,
        "high_conf_accuracy": 0.99}}}
    run = _event(_engine(ledger=ledger), "f3")
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
        handle_camera_event(_engine(), {"property_id": "lockport-2n"})
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
