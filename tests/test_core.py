import json, os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
os.environ["JEV_SPEND_STATE"] = os.path.join(os.path.dirname(__file__), "spend-test.json")

from jev.autonomy import right_for
from jev.parse import parse_decision
from jev.tiers import decide
from jev.types import Category


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


def test_autonomy_defaults_and_earns():
    assert right_for("Act", {}) == "approve_first"
    led = {"categories": {"Act": {"decisive_n": 30, "decisive_accuracy": 0.96, "high_conf_accuracy": 0.99}}}
    assert right_for("Act", led) == "send_with_log"
    led["categories"]["Act"]["decisive_n"] = 80
    led["categories"]["Act"]["decisive_accuracy"] = 0.98
    assert right_for("Act", led) == "send_with_spotcheck"
