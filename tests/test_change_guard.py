"""The stay-change / money guard (Phase 0, 0a) and the never-earned lock
(0b). Every message here is invented; none comes from guest data."""
import json, os, sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from jev.autonomy import NEVER_EARNED, P1, P3, right_for
from jev.change_guard import HOLDING_REPLY, apply_guard, guarded_class, has_grant_wording
from jev.plays.engine import STAKES
from jev.tiers import _ladder, decide
from jev.types import Category, Decision, FAIL_SAFE

POLICIES = ("spark_only", "wonder_only", "escalate", "open_escalate")


@pytest.fixture(autouse=True)
def _offline(tmp_path, monkeypatch):
    # No keys: nothing here can make a live, paid call.
    monkeypatch.delenv("META_MODEL_API_KEY", raising=False)
    monkeypatch.delenv("BASETEN_API_KEY", raising=False)
    monkeypatch.setenv("JEV_SPEND_STATE", str(tmp_path / "spend.json"))


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


def verdict(cat, conf, reason="r"):
    return json.dumps({"category": cat, "confidence": conf, "route": "cleaner", "reason": reason})


def run(policy, message, bodies):
    c = FakeClient(bodies)
    # One client serves both APIs, so the open tier gets the same bodies.
    return decide(policy, message, {"property": "PROP_01"}, client=c, open_client=c)


GUARDED = [
    ("Can we check out at 1pm instead of 11?", "stay change: late checkout"),
    ("Any chance of a late checkout on Sunday?", "stay change: late checkout"),
    ("Could we check in early? Our flight lands in the morning.", "stay change: early check-in"),
    ("Would it be ok to check in at 1pm?", "stay change: early check-in"),
    ("We'd like to add another night if it's open.", "stay change: extension"),
    ("We need to shorten our stay by two nights.", "stay change: dates or length"),
    ("Is it possible to change our dates to the following weekend?", "stay change: dates or length"),
    ("We have to cancel the reservation, sorry.", "stay change: cancellation"),
    ("Why was there a cancellation fee?", "stay change: cancellation"),
    ("Can we get a refund for the last night?", "money: refund"),
    ("Is there a discount if we stay the whole week?", "money: discount or price"),
    ("Could you adjust the price for us?", "money: discount or price"),
    ("The cleaning fee seems high to me.", "money: fee or charge"),
    ("They are still going to charge my card, is that right?", "money: fee or charge"),
    ("How about noon for an extra $40?", "money: amount"),
    ("¿Podemos hacer el check out más tarde?", "stay change: stay change, Spanish"),
    ("¿Nos pueden dar un reembolso?", "money: refund"),
]


@pytest.mark.parametrize("policy", POLICIES)
@pytest.mark.parametrize("message,label", GUARDED)
def test_guard_forces_confirm_over_act_at_0_99(policy, message, label):
    d = run(policy, message, [verdict("Act", 0.99), "Sure, that works!"])
    assert d.category is Category.CONFIRM
    assert d.confidence == 0.99               # the model's number, kept
    assert d.draft == HOLDING_REPLY           # the yes is never handed on
    assert d.route == "host"
    assert d.reason.startswith(f"Guard ({label})")
    assert guarded_class(message) == label


@pytest.mark.parametrize("policy", POLICIES)
def test_guard_forces_confirm_over_ignore(policy):
    d = run(policy, "We canceled, so please send the refund.", [verdict("Ignore", 0.99)])
    assert d.category is Category.CONFIRM and d.confidence == 0.99
    assert d.draft == ""                      # Ignore is never drafted


def test_guard_keeps_a_draft_that_grants_nothing():
    d = run("escalate", "Can we check out at 2pm?",
            [verdict("Act", 0.95), "Let me look at the cleaning schedule for that day."])
    assert d.category is Category.CONFIRM
    assert d.draft == "Let me look at the cleaning schedule for that day."


def test_guard_lints_a_spanish_yes():
    d = run("escalate", "¿Podemos salir más tarde el domingo?",
            [verdict("Act", 0.95), "Claro, pueden salir a la 1."])
    assert d.category is Category.CONFIRM and d.draft == HOLDING_REPLY


ORDINARY_ACTS = [
    "We're locked out, the door code isn't working.",
    "We can't get in, the keypad is dead.",
    "Just wanted to confirm you got the payment! Also the door isn't unlocking.",
    "Water is leaking under the kitchen sink.",
    "The smoke alarm keeps going off.",
    "Running behind, we'll get there around 6pm.",
    "The Wi-Fi is down and we both work remotely.",
]


@pytest.mark.parametrize("policy", POLICIES)
@pytest.mark.parametrize("message", ORDINARY_ACTS)
def test_guard_leaves_ordinary_acts_exactly_as_the_ladder_made_them(policy, message):
    bodies = [verdict("Act", 0.99), "Sure, someone is on the way now."]
    c1, c2 = FakeClient(bodies), FakeClient(bodies)
    guarded = decide(policy, message, {"property": "PROP_01"}, client=c1, open_client=c1)
    ladder = _ladder(policy, message, {"property": "PROP_01"}, client=c2, open_client=c2)
    assert guarded == ladder
    assert guarded.category is Category.ACT
    assert guarded.draft == "Sure, someone is on the way now."   # not linted
    assert guarded_class(message) is None


def test_guard_leaves_ignore_on_ordinary_messages():
    d = run("escalate", "Thanks so much, we loved it!", [verdict("Ignore", 0.95)])
    assert d.category is Category.IGNORE and d.confidence == 0.95


def test_fail_safe_is_untouched_on_a_guarded_message():
    # Dead client: the very same FAIL_SAFE object comes back.
    d = decide("spark_only", "Can we get a refund?", {}, client=FakeClient([None]))
    assert d is FAIL_SAFE
    # Escalation failure keeps its honest tier path and cost.
    d = decide("escalate", "Can we check out at 2pm?", {},
               client=FakeClient([verdict("Act", 0.3), None]))
    assert d.reason == FAIL_SAFE.reason and d.category is Category.CONFIRM
    assert "escalation failed" in d.tier_path and d.cost_usd > 0
    # No key at all: still the plain fail-safe.
    d = decide("escalate", "We're locked out and want a refund.", {})
    assert d.category is Category.CONFIRM and d.reason == FAIL_SAFE.reason


def test_a_confirm_verdict_comes_back_as_the_same_object():
    d = Decision(Category.CONFIRM, 0.9, draft="Sure!", reason="model")
    assert apply_guard(d, "Can we get a refund?") is d


def test_host_words_are_not_the_guests_ask():
    reaction = 'GUEST_01 wrote:\nReacted 👍 to "Reminder: late checkout is $25 after 11 AM."'
    assert guarded_class(reaction) is None
    d = run("escalate", reaction, [verdict("Ignore", 0.95)])
    assert d.category is Category.IGNORE
    host_turn = "GUEST_01 wrote:\nThanks!\n## HOST_01\nHost\nLate checkout is a $25 fee."
    assert guarded_class(host_turn) is None
    guest_after = host_turn + "\n## GUEST_01\nGuest\nCan we get a refund instead?"
    assert guarded_class(guest_after) == "money: refund"


@pytest.mark.parametrize("message", [
    "Thanks so much!", "Is there parking on the street?", "Where do we leave the keys?",
    "Can I charge my phone in the car?", "Is there coffee in the kitchen?",
    "We arrive at 7pm tonight.", "The heat is out.",
])
def test_ordinary_messages_are_not_guarded(message):
    assert guarded_class(message) is None


def test_grant_wording():
    for yes in ("Yes, 1pm is fine", "Sure!", "Of course", "No problem at all", "That works",
                "That's fine", "that’s fine", "Sí, claro", "Está bien", "No hay problema"):
        assert has_grant_wording(yes), yes
    for no in ("Let me check on that and get right back to you.",
               "Si necesitas algo, avísame.", ""):
        assert not has_grant_wording(no), no


def _perfect_ledger():
    p3 = {"decisive_n": 10_000, "decisive_accuracy": 1.0, "high_conf_accuracy": 1.0}
    keys = {"Act", "Confirm", "Ignore"} | set(STAKES) | set(NEVER_EARNED)
    return {"categories": {k: dict(p3) for k in keys}}


def test_never_earned_keys_hold_against_a_rigged_perfect_ledger():
    led = _perfect_ledger()
    assert right_for("message_guest", led) == P3     # the rig really earns
    for key in NEVER_EARNED:
        for conf in (None, 0.85, 0.99, 1.0):
            assert right_for(key, led, conf) == P1, (key, conf)


def test_every_money_stake_key_is_never_earned():
    # STAKES marks moving a guest's money as "highest". A money key added
    # there without joining NEVER_EARNED fails here.
    highest = {k for k, stake in STAKES.items() if stake == "highest"}
    assert highest and highest <= NEVER_EARNED
