import json, os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
os.environ["JEV_SPEND_STATE"] = os.path.join(os.path.dirname(__file__), "spend-plays-test.json")

from jev.plays import (FixtureContext, PlayEngine, default_fixture_data,
                       get, match)
from jev.plays.cleaner_done import handle_cleaner_done_event
from jev.types import Category, Decision

DATE = "2026-10-28"


def _engine(ledger=None, log_path=None, data=None):
    return PlayEngine(FixtureContext(data or default_fixture_data()),
                      ledger=ledger or {}, log_path=log_path)


def _event(engine, prop, date=DATE, **extra):
    payload = {"property_id": prop, "date": date,
               "cleaner_id": "cleaner-cristina", "finished_at": "1:32 PM"}
    payload.update(extra)
    return handle_cleaner_done_event(engine, payload)


def _step(run, name):
    return next(s for s in run.steps if s.name == name)


def test_registered_but_never_message_triggered():
    assert get("cleaner_done") is not None
    # A guest message about a cleaner is not this play's trigger: the
    # play is event-triggered only, and decision matching is unchanged.
    assert match(Decision(Category.ACT, 0.9),
                 "The cleaner is done, can we come in?", {}) is None


def test_happy_path_ready_message_held_by_default(tmp_path):
    log = str(tmp_path / "runs.jsonl")
    run = _event(_engine(log_path=log), "cedar-hollow-2n")
    assert run.status == "completed" and run.data["outcome"] == "ready"
    assert _step(run, "wait_cleaner_done").status == "done"
    match_step = _step(run, "match_arrival")
    assert match_step.result["arrival_today"] is True
    assert match_step.result["guest"] == "Dana"
    verify = _step(run, "verify_clean")
    assert verify.result["clean_status"] == "complete"
    assert verify.result["problem_flag"] == ""

    send = _step(run, "send_guest_ready")
    draft = send.result["draft"]
    assert "Dana" in draft and "Studio 2N, Cedar Hollow" in draft
    assert "ready for you now" in draft
    assert "1:32 PM" in draft                    # cleaner's finish time
    assert "4:00 PM" in draft                    # standard check-in
    assert "keypad" in draft                     # door note, from fixture
    assert "driveway" in draft                   # parking note, from fixture
    # Middle right, not earned in an empty ledger: held, nothing sent.
    assert send.required_right == "approve_first"
    assert send.disposition == "hold" and send.status == "held"
    assert send.result["executed"] is False
    assert run.held_steps == ["send_guest_ready"]

    with open(log) as fh:
        records = [json.loads(line) for line in fh if line.strip()]
    assert any(r["event"] == "gate" and r["action_key"] == "message_guest"
               and r["disposition"] == "hold" for r in records)
    assert records[-1]["event"] == "run_completed"


def test_earned_guest_right_sends_simulated():
    ledger = {"categories": {"message_guest": {
        "decisive_n": 30, "decisive_accuracy": 0.96,
        "high_conf_accuracy": 0.99}}}
    run = _event(_engine(ledger=ledger), "cedar-hollow-2n")
    send = _step(run, "send_guest_ready")
    assert send.required_right == "send_with_log"
    assert send.status == "done"
    assert send.result["executed"] is True
    assert send.result["channel"] == "simulated"
    assert run.status == "completed" and run.held_steps == []


def test_problem_flag_holds_for_human_and_sends_nothing(tmp_path):
    log = str(tmp_path / "runs.jsonl")
    run = _event(_engine(log_path=log), "peters-house")
    assert run.status == "held_for_human"
    assert "Cracked wine glass" in run.hold_reason
    verify = _step(run, "verify_clean")
    assert "Cracked wine glass" in verify.result["problem_flag"]
    # The send step was never reached: no draft, nothing executed.
    send = _step(run, "send_guest_ready")
    assert send.status == "pending" and send.result == {}

    with open(log) as fh:
        records = [json.loads(line) for line in fh if line.strip()]
    assert records[-1]["event"] == "run_held"
    assert "Cracked wine glass" in records[-1]["reason"]


def test_no_arrival_today_logs_and_closes():
    data = default_fixture_data()
    data["clean_reports"]["cedar-hollow-1n|2026-10-28"] = {
        "status": "complete", "finished_at": "11:05 AM",
        "cleaner_id": "cleaner-cristina", "problem_flag": ""}
    run = _event(_engine(data=data), "cedar-hollow-1n")
    assert run.status == "completed" and run.data["outcome"] == "no_arrival"
    assert _step(run, "match_arrival").result["arrival_today"] is False
    assert _step(run, "verify_clean").status == "skipped"
    assert _step(run, "send_guest_ready").status == "skipped"
    assert run.held_steps == []


def test_event_claim_without_completed_clean_fails_safe():
    data = default_fixture_data()
    del data["clean_reports"]["cedar-hollow-2n|2026-10-28"]
    run = _event(_engine(data=data), "cedar-hollow-2n")
    assert run.status == "held_for_human"
    assert "no completed clean" in run.hold_reason
    assert _step(run, "send_guest_ready").status == "pending"


def test_unknown_property_event_fails_safe():
    run = _event(_engine(), "no-such-property")
    assert run.status == "held_for_human"
    assert run.hold_reason
    assert _step(run, "send_guest_ready").status == "pending"
