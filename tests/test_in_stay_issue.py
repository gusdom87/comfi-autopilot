import json, os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
os.environ["JEV_SPEND_STATE"] = os.path.join(os.path.dirname(__file__), "spend-plays-test.json")

from jev.plays import FixtureContext, PlayEngine, default_fixture_data, get, match
from jev.plays.in_stay_issue import InStayIssue, classify_issue
from jev.types import Category, Decision

# Alex is mid-stay at lockport-1n on 2026-10-29 (fixture stay record).
PROP, DATE = "lockport-1n", "2026-10-29"
LEAK_MSG = ("Hi, there's water leaking under the kitchen sink - it's "
            "dripping steadily and the cabinet floor is wet.")
GAS_MSG = "Hi, I smell gas near the stove, kind of strong. Should I be worried?"
BOOKING_MSG = ("We need to leave a day early - can we shorten the stay "
               "and get a refund for the last night?")


def _engine(ledger=None, log_path=None, data=None):
    return PlayEngine(FixtureContext(data or default_fixture_data()),
                      ledger=ledger or {}, log_path=log_path)


def _start(engine, message=LEAK_MSG, prop=PROP, date=DATE, decision=None):
    return engine.start(get("in_stay_issue"),
                        decision or Decision(Category.ACT, 0.93),
                        message, prop, date)


def _step(run, name):
    return next(s for s in run.steps if s.name == name)


def test_registered_and_trigger_matching():
    assert get("in_stay_issue") is not None
    play = InStayIssue()
    assert play.trigger(Decision(Category.ACT, 0.9), LEAK_MSG, {}) is True
    # The registry routes an Act leak here (after early check-in has
    # had its chance and passed).
    routed = match(Decision(Category.ACT, 0.9), LEAK_MSG, {})
    assert routed is not None and routed.name == "in_stay_issue"
    # Confirm is a human's job already - this play needs Act.
    assert play.trigger(Decision(Category.CONFIRM, 0.9), LEAK_MSG, {}) is False
    assert play.trigger(Decision(Category.IGNORE, 0.9), LEAK_MSG, {}) is False


def test_classify_issue_table():
    assert classify_issue(LEAK_MSG).type == "leak"
    assert classify_issue(GAS_MSG).type == "gas_smell"
    assert classify_issue(GAS_MSG).safety is True
    assert classify_issue("The door code is not working, we're outside "
                            "with our bags").type == "lock"
    assert classify_issue("There's no hot water at all this morning").type \
        == "no_hot_water"
    assert classify_issue("The AC is not working and the house won't "
                            "cool down").type == "no_ac"
    assert classify_issue("The dishwasher is broken and won't run").type \
        == "appliance"
    # Severity wins: pouring water is flooding, not a leak.
    assert classify_issue("Water is pouring everywhere in the "
                            "bathroom!").type == "flooding"
    # Not in-home problems at all.
    assert classify_issue(BOOKING_MSG) is None
    assert classify_issue("Can we check in at 1pm today?") is None
    assert classify_issue("Thanks, we had a great stay!") is None


def test_happy_path_dispatch_held_at_p1(tmp_path):
    log = str(tmp_path / "runs.jsonl")
    engine = _engine(log_path=log)
    run = _start(engine)
    assert run.status == "waiting"          # parked for the vendor
    assert run.data["outcome"] == "leak"

    extract = _step(run, "extract_issue")
    assert extract.result["issue_type"] == "leak"
    assert extract.result["safety"] is False

    reality = _step(run, "reality_check")
    assert reality.result["stay_in_progress"] is True
    assert reality.result["guest"] == "Alex"
    assert reality.result["vendor"] == "Fixture Handyman"

    reply = _step(run, "guest_reply")
    assert reply.action_key == "message_guest" and reply.stake == "middle"
    assert "sorry" in reply.result["draft"]
    assert "towel" in reply.result["draft"]          # the self-help line
    assert "handyman" in reply.result["draft"]
    assert reply.disposition == "hold" and reply.status == "held"
    assert reply.result["executed"] is False

    # A standard issue never flags the operator.
    assert _step(run, "notify_operator").status == "skipped"

    dispatch = _step(run, "dispatch_vendor")
    assert dispatch.action_key == "dispatch_vendor"
    assert dispatch.stake == "middle"
    assert dispatch.required_right == "approve_first"
    assert dispatch.disposition == "hold" and dispatch.status == "held"
    assert dispatch.result["executed"] is False
    assert dispatch.result["held_for_approval"] is True
    draft = dispatch.result["draft"]
    assert "Owner's Unit 1N" in draft and "leak" in draft
    assert "Water shutoff" in dispatch.result["access_notes"]
    assert "ETA" in draft
    assert "guest_reply" in run.held_steps
    assert "dispatch_vendor" in run.held_steps

    assert _step(run, "wait_vendor_eta").status == "waiting"
    with open(log) as fh:
        records = [json.loads(line) for line in fh if line.strip()]
    assert any(r["event"] == "gate" and r["action_key"] == "dispatch_vendor"
               and r["disposition"] == "hold" for r in records)


def test_earned_right_dispatch_executes_simulated():
    ledger = {"categories": {"dispatch_vendor": {
        "decisive_n": 30, "decisive_accuracy": 0.96,
        "high_conf_accuracy": 0.99}}}
    run = _start(_engine(ledger=ledger))
    dispatch = _step(run, "dispatch_vendor")
    assert dispatch.required_right == "send_with_log"
    assert dispatch.disposition == "execute" and dispatch.status == "done"
    assert dispatch.result["executed"] is True
    assert dispatch.result["channel"] == "simulated"
    # The guest reply right was not earned - it stays held.
    reply = _step(run, "guest_reply")
    assert reply.status == "held" and reply.result["executed"] is False


def test_safety_branch_notifies_operator_and_skips_self_help():
    run = _start(_engine(), message=GAS_MSG)
    assert run.status == "waiting"
    assert run.data["outcome"] == "safety_gas_smell"
    assert run.data["safety"] is True

    reply = _step(run, "guest_reply")
    draft = reply.result["draft"]
    assert reply.result["safety"] is True
    # Safety guidance, never fix-it self-help.
    assert "head outside" in draft and "911" in draft
    for self_help in ("towel", "thermostat", "try the code"):
        assert self_help not in draft

    notify = _step(run, "notify_operator")
    assert notify.status == "held"          # produced, gated, preserved
    assert notify.action_key == "notify_operator" and notify.stake == "middle"
    assert notify.result["executed"] is False
    summary = notify.result["summary"]
    assert summary.startswith("SAFETY")
    assert "Owner's Unit 1N" in summary and "gas smell" in summary
    assert "notify_operator" in run.held_steps

    dispatch = _step(run, "dispatch_vendor")
    assert dispatch.status == "held"
    assert "SAFETY" in dispatch.result["draft"]


def test_vendor_events_complete_the_followup():
    engine = _engine()
    run = _start(engine)
    engine.handle_event(run, "vendor_eta", {"eta": "3:30 PM"})
    assert run.status == "waiting"          # now parked for done
    assert _step(run, "wait_vendor_eta").status == "done"
    update = _step(run, "guest_eta_update")
    assert update.status == "held"          # message_guest still P1
    assert "3:30 PM" in update.result["draft"]
    assert "on the way" in update.result["draft"]
    assert update.result["executed"] is False
    assert _step(run, "wait_vendor_done").status == "waiting"

    engine.handle_event(run, "vendor_done", {"finished_at": "4:05 PM"})
    assert run.status == "completed"
    assert _step(run, "wait_vendor_done").status == "done"
    done = _step(run, "guest_done_update")
    assert "fixed" in done.result["draft"]
    assert "works now" in done.result["draft"]
    assert done.status == "held"


def test_vendor_done_without_eta_skips_the_eta_update():
    engine = _engine()
    run = _start(engine)
    engine.handle_event(run, "vendor_done", {"finished_at": "4:05 PM"})
    assert run.status == "completed"
    assert _step(run, "wait_vendor_eta").status == "skipped"
    assert _step(run, "guest_eta_update").status == "skipped"
    done = _step(run, "guest_done_update")
    assert "fixed" in done.result["draft"]


def test_non_issue_act_does_not_trigger():
    play = InStayIssue()
    assert play.trigger(Decision(Category.ACT, 0.9), BOOKING_MSG, {}) is False
    assert match(Decision(Category.ACT, 0.9), BOOKING_MSG, {}) is None


def test_no_stay_in_progress_holds_for_human():
    # lockport-2n on 2026-10-28 has an ARRIVAL (Dana) but no stay in
    # progress - nobody is in residence to dispatch help to yet.
    run = _start(_engine(), prop="lockport-2n", date="2026-10-28")
    assert run.status == "held_for_human"
    assert "stay" in run.hold_reason
    reality = _step(run, "reality_check")
    assert reality.result["stay_in_progress"] is False
    for name in ("guest_reply", "dispatch_vendor"):
        step = _step(run, name)
        assert step.status == "pending" and step.result == {}


def test_unidentifiable_issue_fails_safe():
    # Started directly (the sandbox path) with a message that carries
    # no in-home problem: extraction fails safe, nothing is drafted.
    run = _start(_engine(), message="The view is lovely, thanks!")
    assert run.status == "held_for_human"
    assert "issue" in run.hold_reason
    assert _step(run, "extract_issue").status == "failed"
    assert _step(run, "guest_reply").status == "pending"
