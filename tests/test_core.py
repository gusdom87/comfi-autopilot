import json, os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
os.environ["JEV_SPEND_STATE"] = os.path.join(os.path.dirname(__file__), "spend-test.json")

from jev.autonomy import right_for
from jev.parse import parse_decision
from jev.tiers import decide
from jev.types import Category, FAIL_SAFE


class FakeClient:
    """Returns canned Responses-API bodies in order."""
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
        return {"output_text": text, "usage": {"input_tokens": 100, "output_tokens": 20}}


def verdict(cat, conf):
    return json.dumps({"category": cat, "confidence": conf, "route": "host", "reason": "r"})


def test_parse_garbage_is_none():
    assert parse_decision("not json at all", "m") is None
    assert parse_decision('{"category": "Bogus", "confidence": 0.5}', "m") is None


def test_spark_high_confidence_no_escalation():
    c = FakeClient([verdict("Confirm", 0.9)])
    d = decide("escalate", "Can we check in early?", {}, client=c)
    assert d.category is Category.CONFIRM and c.calls == 1


def test_low_confidence_escalates_and_drafts():
    c = FakeClient([verdict("Confirm", 0.4), verdict("Act", 0.88), "On it, cleaner is headed over."])
    d = decide("escalate", "Water is leaking under the sink", {}, client=c)
    assert d.category is Category.ACT and c.calls == 3
    assert "->" in d.tier_path and d.draft.startswith("On it")


def test_dead_client_fails_safe():
    c = FakeClient([None])
    d = decide("spark_only", "Hello?", {}, client=c)
    assert d.category is Category.CONFIRM and d.confidence == 0.0


def test_escalation_failure_fails_safe_not_fast_verdict():
    # Spark is below the threshold and Wonder fails: the too-uncertain
    # fast verdict is not handed back as the decision.
    c = FakeClient([verdict("Ignore", 0.3), None])
    d = decide("escalate", "ok thanks", {}, client=c)
    assert c.calls == 2
    assert d.category is Category.CONFIRM and d.confidence == 0.0
    assert d.reason == FAIL_SAFE.reason
    assert "escalation failed" in d.tier_path
    assert d.cost_usd > 0          # the fast call's spend is still reported
    assert FAIL_SAFE.tier_path == "" and FAIL_SAFE.cost_usd == 0.0


def test_autonomy_defaults_and_earns():
    assert right_for("Act", {}) == "approve_first"
    led = {"categories": {"Act": {"decisive_n": 30, "decisive_accuracy": 0.96, "high_conf_accuracy": 0.99}}}
    assert right_for("Act", led) == "send_with_log"
    led["categories"]["Act"]["decisive_n"] = 80
    led["categories"]["Act"]["decisive_accuracy"] = 0.98
    assert right_for("Act", led) == "send_with_spotcheck"


def test_autonomy_floor_holds_low_confidence_decisions():
    led = {"categories": {"Ignore": {"decisive_n": 80, "decisive_accuracy": 0.98, "high_conf_accuracy": 0.99}}}
    # An earned right never covers a decision below the 0.85 floor.
    assert right_for("Ignore", led, 0.30) == "approve_first"
    assert right_for("Ignore", led, 0.84) == "approve_first"
    assert right_for("Ignore", led, 0.85) == "send_with_spotcheck"
    assert right_for("Ignore", led) == "send_with_spotcheck"


def _p3_for_every_key():
    p3 = {"decisive_n": 200, "decisive_accuracy": 1.0, "high_conf_accuracy": 1.0}
    keys = ("Act", "Confirm", "Ignore", "auto_close", "message_guest",
            "message_cleaner", "dispatch_vendor", "notify_operator", "charge_guest")
    return {"categories": {k: dict(p3) for k in keys}}


def test_charge_guest_is_never_earned():
    led = _p3_for_every_key()
    # Stats that earn P3 anywhere else still leave charging a guest at P1.
    assert right_for("charge_guest", led) == "approve_first"
    assert right_for("charge_guest", led, 0.99) == "approve_first"
    assert right_for("charge_guest", led, 1.0) == "approve_first"


def test_charge_guest_lock_is_key_specific():
    led = _p3_for_every_key()
    assert right_for("message_guest", led) == "send_with_spotcheck"
    assert right_for("message_guest", led, 0.99) == "send_with_spotcheck"
