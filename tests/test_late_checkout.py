import json, os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
os.environ["JEV_SPEND_STATE"] = os.path.join(os.path.dirname(__file__), "spend-plays-test.json")

from jev.plays import (FixtureContext, PlayEngine, default_fixture_data,
                       extract_requested_checkout, get, match)
from jev.plays.config import PlaysConfig
from jev.plays.late_checkout import LateCheckout
from jev.types import Category, Decision

# 2026-11-08: Riley departs cedar-1a with nobody arriving (clear day);
# Casey departs maple-house and Morgan arrives at 4 PM, with a
# 180-minute turnover + 30-minute buffer between -> latest 12:30 PM.
DATE = "2026-11-08"
CLEAR, BUSY = "cedar-1a", "maple-house"
MSG = ("Would it be possible to check out at 1pm instead of 11? Our "
       "flight home is late afternoon.")
VAGUE = "Any chance of a late checkout on our last day?"


def _engine(ledger=None, log_path=None, data=None, config=None):
    return PlayEngine(FixtureContext(data or default_fixture_data()),
                      ledger=ledger or {}, log_path=log_path, config=config)


def _run(engine, prop=CLEAR, msg=MSG, date=DATE, decision=None):
    return engine.start(get("late_checkout"),
                        decision or Decision(Category.ACT, 0.9),
                        msg, prop, date)


def _step(run, name):
    return next(s for s in run.steps if s.name == name)


def _draft(run):
    return _step(run, "guest_reply").result["draft"]


def test_registered_and_trigger_matching():
    assert get("late_checkout") is not None
    play = LateCheckout()
    act = Decision(Category.ACT, 0.9)
    assert play.trigger(act, MSG, {}) is True
    assert play.trigger(act, VAGUE, {}) is True
    assert play.trigger(Decision(Category.CONFIRM, 0.9), MSG, {}) is True
    assert play.trigger(Decision(Category.IGNORE, 0.9), MSG, {}) is False
    assert match(act, MSG, {}).name == "late_checkout"
    # A late-checkout ask that mentions an arrival is still this play,
    # not early check-in (whose cue list includes "get there").
    assert match(act, "Could we check out at 1pm? Our ride won't get "
                      "there until 1:30pm.", {}).name == "late_checkout"
    # Everything else routes exactly as before.
    assert match(act, "Hi, can we check in at 1pm?", {}).name == "early_checkin"
    assert match(act, "Could we check in early and maybe check out "
                      "at 1pm?", {}).name == "early_checkin"
    assert match(act, "Water is leaking under the sink and we have to "
                      "check out later", {}).name == "in_stay_issue"
    # A booking extension is a calendar and money change, not this play.
    assert play.trigger(act, "Could we stay another night and check out "
                             "at 2pm on Monday?", {}) is False
    assert play.trigger(act, "Thanks for a great stay! We'll be out by "
                             "10am.", {}) is False
    for msg in ("Can we stay longer? Maybe two more nights",
                "Could we stay longer, through Tuesday?"):
        assert play.trigger(act, msg, {}) is False, msg


def test_extract_checkout_times():
    std = "11:00"
    assert extract_requested_checkout("check out at 1pm", std) == 780
    assert extract_requested_checkout("could we stay until noon?", std) == 720
    assert extract_requested_checkout("check out a bit later, like "
                                      "1:30 PM", std) == 810
    assert extract_requested_checkout("check out an hour later?", std) == 720
    assert extract_requested_checkout("can we have an extra hour at "
                                      "checkout", std) == 720
    assert extract_requested_checkout("could we stay 2 hours longer", std) == 780
    # The standard time named as a reference is not the ask.
    assert extract_requested_checkout("instead of checking out at 11am, "
                                      "could we check out at 1pm?", std) == 780
    # Never guessed: no time, a time not tied to leaving, two different
    # times, a nonsense time, a time that is not later than standard.
    assert extract_requested_checkout("any chance of a late checkout?", std) is None
    assert extract_requested_checkout("late checkout, our flight is at "
                                      "7pm", std) is None
    assert extract_requested_checkout("check out at 1pm or stay until "
                                      "3pm", std) is None
    assert extract_requested_checkout("check out at 25pm", std) is None
    assert extract_requested_checkout("check out at 1:75pm", std) is None
    assert extract_requested_checkout("we'll check out at 10am", std) is None
    assert extract_requested_checkout("check out at 1", std) is None
    # A trip's time is never the checkout ask; an ask beside it still is.
    assert extract_requested_checkout("Our flight is leaving at 7pm, any "
                                      "chance of a late checkout?", std) is None
    assert extract_requested_checkout("can we come by at 2pm to drop our "
                                      "bags? We're leaving at 1pm from the "
                                      "airport", std) is None
    assert extract_requested_checkout("Could we check out at 1pm since our "
                                      "flight is at 6pm?", std) == 780
    # One anchored time plus another later time: not settled, so ask.
    assert extract_requested_checkout("Could we check out at 1pm or 2pm?",
                                      std) is None
    assert extract_requested_checkout("Can we check out at 1pm? Actually "
                                      "2pm would be better.", std) is None


def test_flight_time_is_never_the_checkout_ask():
    act = Decision(Category.ACT, 0.9)
    flight = "Our flight is leaving at 7pm, any chance of a late checkout?"
    assert match(act, flight, {}).name == "late_checkout"
    run = _run(_engine(), msg=flight)
    assert run.data["outcome"] == "clarify"
    assert run.data["requested_minutes"] is None
    assert "7:00 PM" not in _draft(run)
    assert _step(run, "late_checkout_charge").status == "skipped"
    # A bag-drop ask whose only leaving time is the flight's is early
    # check-in's again, not a 1 PM checkout.
    assert match(act, "can we come by at 2pm to drop our bags? We're "
                      "leaving at 1pm from the airport", {}).name == \
        "early_checkin"


def test_clear_day_yes_for_requested_time():
    run = _run(_engine())
    assert run.status == "completed" and run.data["outcome"] == "grant"
    assert _step(run, "extract_request").result["requested_checkout"] == "1:00 PM"
    reality = _step(run, "reality_check").result
    assert reality["same_day_arrival"] is False
    assert reality["same_day_turnover"] is False
    assert reality["guest"] == "Riley"
    assert reality["latest_checkout"] == "2:00 PM"        # house rule
    assert reality["limit"] == "house_rule"
    draft = _draft(run)
    assert "Riley" in draft and "1:00 PM checkout works" in draft
    assert "late checkout fee (amount to be set)" in draft
    assert "$" not in draft and "separate request you can accept" in draft
    reply = _step(run, "guest_reply")
    assert reply.action_key == "message_guest"
    assert reply.status == "held" and reply.result["executed"] is False
    # Nobody arrives and no turnover is booked: no cleaner to tell.
    assert _step(run, "cleaner_heads_up").status == "skipped"


def test_same_day_arrival_no_offers_latest_workable_time():
    run = _run(_engine(), prop=BUSY)
    assert run.status == "completed"
    assert run.data["outcome"] == "deny_offer"
    reality = _step(run, "reality_check").result
    assert reality["same_day_arrival"] is True
    assert reality["next_guest"] == "Morgan"
    assert reality["next_checkin"] == "4:00 PM"
    assert reality["cleaner"] == "Tomas"
    # 4:00 PM check-in - 180 min turnover - 30 min buffer = 12:30 PM.
    assert reality["latest_checkout"] == "12:30 PM"
    assert reality["limit"] == "same_day_arrival"
    draft = _draft(run)
    assert "Casey" in draft
    assert "1:00 PM won't work" in draft
    assert "guests arriving after you" in draft
    assert "The latest I can do is 12:30 PM" in draft
    assert "once you confirm" in draft            # the fee waits for a yes
    assert _step(run, "guest_reply").status == "held"
    # An offer the guest has not taken: no charge, no schedule change.
    for name in ("late_checkout_charge", "cleaner_heads_up"):
        step = _step(run, name)
        assert step.status == "skipped" and step.result == {}


def test_turnover_boundary_and_offered_time_is_feasible():
    engine = _engine()
    # Exactly at the turnover deadline: fits.
    at = _run(engine, prop=BUSY, msg="Could we check out at 12:30pm?")
    assert at.data["outcome"] == "grant"
    # One minute past it: a collision, and the offer is the deadline.
    past = _run(engine, prop=BUSY, msg="Could we check out at 12:31pm?")
    assert past.data["outcome"] == "deny_offer"
    assert "The latest I can do is 12:30 PM" in _draft(past)
    # The guest takes the offered time: under the same constraints it
    # is a yes, and only now does the fee step appear.
    took = _run(engine, prop=BUSY, msg="12:30pm works - we'll check out "
                                        "at 12:30pm then")
    assert took.data["outcome"] == "grant"
    assert _step(took, "late_checkout_charge").status == "held"


def test_fee_step_held_and_never_executed_at_p1(tmp_path):
    log = str(tmp_path / "runs.jsonl")
    run = _run(_engine(log_path=log))
    charge = _step(run, "late_checkout_charge")
    assert charge.kind == "action" and charge.action_key == "charge_guest"
    assert charge.stake == "highest"
    assert charge.required_right == "approve_first"
    assert charge.disposition == "hold" and charge.status == "held"
    assert charge.result["executed"] is False
    assert charge.result["held_for_approval"] is True
    # No amount configured: the step is drafted and held with the
    # amount left to the operator, and no dollar figure anywhere.
    assert charge.result["charge"] == {
        "amount_usd": None,
        "description": "Late checkout fee (amount to be set)",
        "property_id": CLEAR, "date": DATE, "checkout_time": "1:00 PM"}
    assert "$" not in json.dumps(run.to_dict())
    # A separate step from the guest reply, and both await approval.
    assert run.held_steps == ["guest_reply", "late_checkout_charge"]
    with open(log) as fh:
        records = [json.loads(line) for line in fh if line.strip()]
    assert any(r["event"] == "gate" and r["action_key"] == "charge_guest"
               and r["disposition"] == "hold" for r in records)
    # No record anywhere in the run says money moved.
    assert not any(r.get("result", {}).get("executed") for r in records)
    assert records[-1]["event"] == "run_completed"
    assert "$" not in json.dumps(records)


def test_fee_gated_identically_to_early_checkin():
    for ledger in ({}, {"categories": {"charge_guest": {
            "decisive_n": 80, "decisive_accuracy": 0.98,
            "high_conf_accuracy": 0.99}}}):
        late = _step(_run(_engine(ledger=ledger)), "late_checkout_charge")
        early = _step(_engine(ledger=ledger).start(
            get("early_checkin"), Decision(Category.ACT, 0.9),
            "Can we check in at 1pm?", "cedar-1a", "2026-10-28"),
            "early_checkin_charge")
        assert (late.required_right, late.disposition, late.status) == \
            (early.required_right, early.disposition, early.status)
        assert late.result["executed"] == early.result["executed"]


def test_earned_guest_right_sends_simulated_money_still_held():
    ledger = {"categories": {"message_guest": {
        "decisive_n": 30, "decisive_accuracy": 0.96,
        "high_conf_accuracy": 0.99}}}
    run = _run(_engine(ledger=ledger))
    reply = _step(run, "guest_reply")
    assert reply.required_right == "send_with_log"
    assert reply.status == "done" and reply.result["channel"] == "simulated"
    # Earning one right never leaks into another.
    charge = _step(run, "late_checkout_charge")
    assert charge.status == "held" and charge.result["executed"] is False


def test_first_hour_is_free_no_charge_step():
    run = _run(_engine(), msg="Could we check out at noon?")
    assert run.data["outcome"] == "grant"
    assert "no charge" in _draft(run) and "$" not in _draft(run)
    assert _step(run, "late_checkout_charge").status == "skipped"
    assert run.held_steps == ["guest_reply"]
    run = _run(_engine(), msg="Could we check out at 11:30am?")
    assert "good news - an 11:30 AM checkout works" in _draft(run)


def test_fee_comes_from_config():
    run = _run(_engine(config=PlaysConfig(late_checkout_fee_usd=40.0)))
    assert _step(run, "late_checkout_charge").result["charge"]["amount_usd"] == 40.0
    assert "$40" in _draft(run)


def test_fee_amount_only_when_env_sets_it(monkeypatch):
    monkeypatch.delenv("JEV_LATE_CHECKOUT_FEE_USD", raising=False)
    assert PlaysConfig.from_env().late_checkout_fee_usd is None
    monkeypatch.setenv("JEV_LATE_CHECKOUT_FEE_USD", "30")
    run = _run(_engine(config=PlaysConfig.from_env()))
    charge = _step(run, "late_checkout_charge").result["charge"]
    assert charge["amount_usd"] == 30.0
    assert charge["description"] == "Late checkout fee"
    assert "The late checkout fee is $30" in _draft(run)


def test_ambiguous_request_clarifies():
    run = _run(_engine(), msg=VAGUE)
    assert run.status == "completed"               # not a failure
    assert run.data["outcome"] == "clarify"
    assert run.data["requested_minutes"] is None
    assert _step(run, "extract_request").result["requested_checkout"] == ""
    draft = _draft(run)
    assert "what time were you hoping to leave?" in draft
    assert "The latest I can do that day is 2:00 PM" in draft
    assert "12:00 PM is free" in draft
    assert "late checkout fee (amount to be set)" in draft and "$" not in draft
    assert _step(run, "guest_reply").status == "held"
    # Nothing priced or scheduled on a time nobody named.
    for name in ("late_checkout_charge", "cleaner_heads_up"):
        assert _step(run, name).status == "skipped"
    # Two different times, or a nonsense one, never guess either: the
    # trigger does not claim them, and started by name they hold.
    for msg in ("Could we check out at 1pm, or stay until 3pm?",
                "Could we check out at 25pm?"):
        run = _run(_engine(), msg=msg)
        assert run.status == "held_for_human"
        assert "not a late-checkout ask" in run.hold_reason
        assert _step(run, "guest_reply").status == "pending"


def test_ambiguous_on_a_day_with_no_slack_is_a_plain_no():
    # When nothing later than standard checkout works, the answer does
    # not depend on the time, so the play does not ask for one.
    data = default_fixture_data()
    data["arrivals"]["maple-house|2026-11-08"]["checkin_time"] = "13:00"
    run = _run(_engine(data=data), prop=BUSY, msg=VAGUE)
    assert run.data["outcome"] == "deny"
    assert "can't do a late checkout" in _draft(run)
    assert "Checkout stays at 11:00 AM" in _draft(run)
    assert "?" not in _draft(run)


def test_collision_with_no_slack_denies_without_an_offer():
    data = default_fixture_data()
    data["arrivals"]["maple-house|2026-11-08"]["checkin_time"] = "13:00"
    run = _run(_engine(data=data), prop=BUSY)
    # 1:00 PM check-in - 180 - 30 = 9:30 AM: no later time works at all.
    assert run.data["outcome"] == "deny"
    assert "latest I can do" not in _draft(run)
    for name in ("late_checkout_charge", "cleaner_heads_up"):
        assert _step(run, name).status == "skipped"


def test_overshooting_the_house_rule_grants_the_latest_time():
    run = _run(_engine(), msg="Could we stay until 5pm?")
    assert run.data["outcome"] == "grant_partial"
    draft = _draft(run)
    assert "good news" in draft
    assert "stretch checkout to 2:00 PM, though not quite to 5:00 PM" in draft
    assert "2:00 PM is the latest checkout this place allows" in draft
    assert "once you confirm" in draft
    # The guest asked for 5 PM, not 2 PM: no charge until they say yes.
    assert _step(run, "late_checkout_charge").status == "skipped"


def test_scheduled_work_window_caps_the_checkout():
    data = default_fixture_data()
    data["work_windows"]["cedar-1a|2026-11-08"] = [{
        "label": "Fixture carpet cleaning", "start": "12:30",
        "end": "14:30", "kind": "vendor"}]
    run = _run(_engine(data=data))
    assert run.data["outcome"] == "grant_partial"
    reality = _step(run, "reality_check").result
    assert reality["limit"] == "scheduled_work"
    assert reality["latest_checkout"] == "12:30 PM"
    assert "service visit booked at the place at 12:30 PM" in _draft(run)
    assert "carpet" not in _draft(run)         # vendor detail stays internal


def test_work_window_from_before_checkout_says_right_after_checkout():
    data = default_fixture_data()
    data["work_windows"]["cedar-1a|2026-11-08"] = [{
        "label": "Fixture carpet cleaning", "start": "10:00",
        "end": "12:00", "kind": "vendor"}]
    run = _run(_engine(data=data))
    assert run.data["outcome"] == "deny"
    draft = _draft(run)
    assert "service visit at the place right after checkout" in draft
    assert "10:00 AM" not in draft


def test_turnover_day_grant_gives_cleaner_heads_up():
    run = _run(_engine(), prop=BUSY, msg="Could we check out at noon?")
    assert run.data["outcome"] == "grant"
    heads_up = _step(run, "cleaner_heads_up")
    assert heads_up.action_key == "message_cleaner"
    assert heads_up.status == "held" and heads_up.result["executed"] is False
    assert heads_up.result["cleaner"] == "Tomas"
    draft = heads_up.result["draft"]
    assert "Tomas" not in draft               # never the cleaner's name
    assert "late checkout at 12:00 PM" in draft
    assert "instead of 11:00 AM" in draft
    assert "still checks in at 4:00 PM" in draft
    # Within the free hour: no charge step on this one.
    assert _step(run, "late_checkout_charge").status == "skipped"
    assert run.held_steps == ["guest_reply", "cleaner_heads_up"]


def test_same_day_arrival_without_turnover_flags_the_operator():
    # Avery arrives at cedar-1a the day Riley leaves, and no turnover is
    # on record: no cleaner to tell, so the grant flags the operator.
    data = default_fixture_data()
    data["arrivals"]["cedar-1a|2026-11-08"] = {"guest_name": "Avery"}
    run = _run(_engine(data=data))
    assert run.data["outcome"] == "grant"
    reality = _step(run, "reality_check").result
    assert reality["same_day_arrival"] is True
    assert reality["same_day_turnover"] is False
    assert _step(run, "cleaner_heads_up").status == "skipped"
    notify = _step(run, "notify_operator")
    assert notify.action_key == "notify_operator" and notify.stake == "middle"
    assert notify.status == "held" and notify.result["executed"] is False
    summary = notify.result["summary"]
    assert "no turnover on record" in summary
    assert "confirm who cleans and when" in summary
    assert "Riley checks out at 1:00 PM" in summary and "Avery" in summary
    assert run.held_steps == ["guest_reply", "late_checkout_charge",
                              "notify_operator"]


def test_next_day_arrival_is_not_a_collision():
    data = default_fixture_data()
    data["arrivals"]["cedar-1a|2026-11-09"] = {"guest_name": "Avery"}
    run = _run(_engine(data=data))
    assert run.data["outcome"] == "grant"
    assert _step(run, "reality_check").result["same_day_arrival"] is False


def test_unknown_property_holds():
    run = _run(_engine(), prop="no-such-property")
    assert run.status == "held_for_human"
    assert "unknown property" in run.hold_reason
    assert _step(run, "reality_check").status == "failed"
    for name in ("guest_reply", "late_checkout_charge", "cleaner_heads_up",
                 "notify_operator"):
        step = _step(run, name)
        assert step.status == "pending" and step.result == {}


def test_no_departing_stay_or_date_holds():
    engine = _engine()
    # Dana ARRIVES at cedar-2a on 10-28; nobody departs. Alex is
    # mid-stay at cedar-1a on 10-29 and leaves on the 30th.
    for prop, date in (("cedar-2a", "2026-10-28"), ("cedar-1a", "2026-10-29")):
        run = _run(engine, prop=prop, date=date)
        assert run.status == "held_for_human"
        assert "no stay on record departing" in run.hold_reason
        assert _step(run, "guest_reply").status == "pending"
    run = _run(engine, date="")
    assert run.status == "held_for_human"
    assert "no departure date" in run.hold_reason
