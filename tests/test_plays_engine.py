import json, os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
os.environ["JEV_SPEND_STATE"] = os.path.join(os.path.dirname(__file__), "spend-plays-test.json")

from jev.plays import (FixtureContext, LiveTurnoReadAdapter, PlayEngine,
                       default_fixture_data, get, match)
from jev.plays.context import to_minutes
from jev.tiers import decide
from jev.types import Category, Decision


def _engine(log_path=None, ledger=None):
    return PlayEngine(FixtureContext(default_fixture_data()),
                      ledger=ledger or {}, log_path=log_path)


def _read_log(path):
    with open(path) as fh:
        return [json.loads(line) for line in fh if line.strip()]


def test_registry_and_trigger_matching():
    assert get("early_checkin") is not None
    d = Decision(Category.ACT, 0.9)
    play = match(d, "Hi, can we check in at 1pm?", {})
    assert play is not None and play.name == "early_checkin"
    # Noise never fires a play.
    assert match(Decision(Category.IGNORE, 0.95), "check in at 1pm", {}) is None
    # A leak is Act but not this play: it routes to Play 4
    # (in-stay issues), registered after early check-in.
    leak_play = match(d, "Water is leaking under the sink", {})
    assert leak_play is not None and leak_play.name == "in_stay_issue"


def test_gate_holds_charge_by_default_and_logs_it(tmp_path):
    log = str(tmp_path / "runs.jsonl")
    run = _engine(log_path=log).start(
        get("early_checkin"), Decision(Category.ACT, 0.9),
        "Can we check in at 1pm?", "lockport-1n", "2026-10-28")
    charge = next(s for s in run.steps if s.name == "early_checkin_charge")
    assert charge.required_right == "approve_first"
    assert charge.disposition == "hold" and charge.status == "held"
    assert charge.result["executed"] is False
    assert charge.result["held_for_approval"] is True
    assert charge.stake == "highest"
    records = _read_log(log)
    gates = [r for r in records if r["event"] == "gate"]
    assert any(g["action_key"] == "charge_guest"
               and g["required_right"] == "approve_first"
               and g["disposition"] == "hold" for g in gates)
    assert records[0]["event"] == "run_started"
    assert records[-1]["event"] == "run_completed"


def test_earned_right_executes_charge_simulated():
    ledger = {"categories": {"charge_guest": {
        "decisive_n": 80, "decisive_accuracy": 0.98,
        "high_conf_accuracy": 0.99}}}
    run = _engine(ledger=ledger).start(
        get("early_checkin"), Decision(Category.ACT, 0.9),
        "Can we check in at 1pm?", "lockport-1n", "2026-10-28")
    charge = next(s for s in run.steps if s.name == "early_checkin_charge")
    assert charge.required_right == "send_with_spotcheck"
    assert charge.disposition == "execute" and charge.status == "done"
    assert charge.result["executed"] is True
    assert charge.result["channel"] == "simulated"


def test_unknown_property_fails_safe_to_human():
    run = _engine().start(
        get("early_checkin"), Decision(Category.ACT, 0.9),
        "Can we check in at 1pm?", "no-such-property", "2026-10-28")
    assert run.status == "held_for_human"
    assert run.hold_reason
    reply = next(s for s in run.steps if s.name == "guest_reply")
    assert reply.status == "pending"  # never reached, nothing drafted/sent


def test_live_adapter_is_an_honest_stub():
    adapter = LiveTurnoReadAdapter()
    for call in (lambda: adapter.get_property("x"),
                 lambda: adapter.get_turnover("x", "2026-10-28"),
                 lambda: adapter.ready_time_minutes("x", "2026-10-28")):
        try:
            call()
            raise AssertionError("stub must raise")
        except NotImplementedError as exc:
            assert "stub" in str(exc).lower()


def test_time_math():
    assert to_minutes("16:00") == 960
    ctx = FixtureContext(default_fixture_data())
    # 11:00 checkout + 150 min clean + 30 buffer = 2:00 PM
    assert ctx.ready_time_minutes("lockport-2n", "2026-10-28") == 840
    # No turnover: default ready noon
    assert ctx.ready_time_minutes("lockport-1n", "2026-10-28") == 720
    assert ctx.ready_time_minutes("ghost", "2026-10-28") is None


class _FakeClient:
    def __init__(self, bodies):
        self.bodies = list(bodies)
        self.calls = 0

    def complete(self, model, system, user):
        self.calls += 1
        if not self.bodies:
            return None
        text = self.bodies.pop(0)
        if text is None:
            return None
        return {"output_text": text,
                "usage": {"input_tokens": 100, "output_tokens": 20}}


def test_triage_decision_fires_play_end_to_end_offline():
    verdict = json.dumps({"category": "Act", "confidence": 0.92,
                          "route": "host", "reason": "arrival today"})
    client = _FakeClient([verdict, "draft not used by the play"])
    d = decide("escalate", "Can we check in at 1pm today?",
               {"property": "lockport-1n"}, client=client)
    assert d.category is Category.ACT
    play = match(d, "Can we check in at 1pm today?", {})
    assert play is not None
    run = _engine().start(play, d, "Can we check in at 1pm today?",
                          "lockport-1n", "2026-10-28")
    assert run.status == "completed" and run.data["outcome"] == "grant"
