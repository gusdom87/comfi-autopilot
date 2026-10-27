import json, os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from jev.plays import (FixtureContext, PlayEngine, default_fixture_data,
                       extract_requested_minutes, get)
from jev.plays.config import PlaysConfig
from jev.types import Category, Decision

DATE = "2026-10-28"
MSG = "Hi! Could we check in at 1pm?"


def _engine(ledger=None, config=None, log_path=None):
    return PlayEngine(FixtureContext(default_fixture_data()),
                      ledger=ledger or {}, log_path=log_path, config=config)


def _run(engine, prop, msg=MSG):
    return engine.start(get("early_checkin"), Decision(Category.ACT, 0.9),
                        msg, prop, DATE)


def _step(run, name):
    return next(s for s in run.steps if s.name == name)


def test_extract_times():
    assert extract_requested_minutes("check in at 1pm", "16:00") == 780
    assert extract_requested_minutes("arrive by 2:30 PM", "16:00") == 870
    assert extract_requested_minutes("can we get in an hour early?", "16:00") == 900
    assert extract_requested_minutes("2 hours earlier would help", "16:00") == 840
    assert extract_requested_minutes("just saying hi", "16:00") is None


def test_grant_when_no_turnover():
    run = _run(_engine(), "lockport-1n")
    assert run.status == "completed" and run.data["outcome"] == "grant"
    reply = _step(run, "guest_reply")
    assert "1:00 PM" in reply.result["draft"]
    assert "works for check-in" in reply.result["draft"]
    charge = _step(run, "early_checkin_charge")
    assert charge.result["charge"]["amount_usd"] == 25.0
    assert charge.status == "held"  # highest right: human approves money
    for name in ("cleaner_ping", "wait_cleaner_done", "guest_ready_update"):
        assert _step(run, name).status == "skipped"


def test_conditional_pings_cleaner_and_waits(tmp_path):
    log = str(tmp_path / "runs.jsonl")
    engine = _engine(log_path=log)
    run = _run(engine, "lockport-2n")
    assert run.data["outcome"] == "conditional"
    assert run.status == "waiting"
    ping = _step(run, "cleaner_ping")
    assert ping.result["cleaner"] == "Cristina"
    assert "reply done here" in ping.result["draft"]
    assert _step(run, "wait_cleaner_done").status == "waiting"

    engine.handle_event(run, "cleaner_done",
                        {"cleaner_id": "cleaner-cristina",
                         "finished_at": "1:05 PM"})
    assert run.status == "completed"
    assert _step(run, "wait_cleaner_done").status == "done"
    ready = _step(run, "guest_ready_update")
    assert "ready for you now" in ready.result["draft"]
    assert "1:05 PM" in ready.result["draft"]

    with open(log) as fh:
        events = [json.loads(line)["event"] for line in fh if line.strip()]
    assert "run_waiting" in events and "event_received" in events
    assert events[-1] == "run_completed"


def test_deny_is_honest_and_charges_nothing():
    run = _run(_engine(), "peters-house")
    assert run.status == "completed" and run.data["outcome"] == "deny"
    reply = _step(run, "guest_reply")
    assert "will not be ready by 1:00 PM" in reply.result["draft"]
    assert _step(run, "early_checkin_charge").status == "skipped"
    assert _step(run, "cleaner_ping").status == "skipped"


def test_fee_comes_from_config():
    run = _run(_engine(config=PlaysConfig(early_checkin_fee_usd=40.0)),
               "lockport-1n")
    charge = _step(run, "early_checkin_charge")
    assert charge.result["charge"]["amount_usd"] == 40.0
    assert "$40" in _step(run, "guest_reply").result["draft"]


def test_earned_guest_right_sends_simulated():
    ledger = {"categories": {"message_guest": {
        "decisive_n": 30, "decisive_accuracy": 0.96,
        "high_conf_accuracy": 0.99}}}
    run = _run(_engine(ledger=ledger), "lockport-1n")
    reply = _step(run, "guest_reply")
    assert reply.required_right == "send_with_log"
    assert reply.status == "done"
    assert reply.result["executed"] is True
    assert reply.result["channel"] == "simulated"
    # Money still held: earning one right never leaks into another.
    assert _step(run, "early_checkin_charge").status == "held"


def test_relative_request_uses_standard_checkin():
    run = _run(_engine(), "lockport-1n", "Can we get in an hour early?")
    assert run.data["requested_minutes"] == 900
    assert run.data["outcome"] == "grant"


def test_unusable_time_fails_safe():
    play = get("early_checkin")
    run = _engine().start(play, Decision(Category.ACT, 0.9),
                          "We would love an early check-in please!",
                          "lockport-1n", DATE)
    # Trigger needs a cue + "early"; extraction then fails on no time.
    assert run.status == "held_for_human"
    assert _step(run, "guest_reply").status == "pending"
