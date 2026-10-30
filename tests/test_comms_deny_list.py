"""Connector deny-list (package L7) and the action-key class map (package
1b, Appendix B). "Never automatic" is tested as "not callable": the
denied operations raise, and no comms module or class carries one in
its public names."""
import importlib
import os
import pkgutil
import sys
from dataclasses import dataclass

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import jev.comms
from jev.autonomy import NEVER_EARNED
from jev.comms import classes, deny_list, transport
from jev.comms.crew_gate import CrewSendGate
from jev.comms.deny_list import (ALLOWED_OPERATIONS, DENIED_OPERATIONS, DeniedOperation,
                                 check_operation, is_denied, surface_violations)
from jev.comms.transport import (MockCrewTransport, MockOperatorTransport,
                                 QuoCliOperatorTransport, RecipientError)
from jev.plays.engine import STAKES

# Package section 5.2, L7, verbatim operation names.
L7_HOSPITABLE = ("lock-smartlock", "unlock-smartlock", "cancel-reservation", "create-quote",
                 "update-reservation", "update-property-calendar", "send-reservation-message")
L7_QUO = ("start-agent-call", "start-agent-conversation", "ask-knowledge-agent",
          "send-bulk-messages", "send-group-message", "fetch-call-transcripts")
APPENDIX_B = ("charge_guest", "refund", "discount", "price_change", "approve_spend",
              "vendor_payment", "purchase_or_reimburse", "deposit_or_damage_claim",
              "airbnb_case_or_resolution", "assign_clean", "share_access", "safety_guest_line")


def comms_modules():
    yield jev.comms
    for info in pkgutil.iter_modules(jev.comms.__path__):
        yield importlib.import_module(f"jev.comms.{info.name}")


def test_every_l7_operation_is_denied_with_a_reason():
    names = [f"hospitable:{n}" for n in L7_HOSPITABLE] + [f"quo:{n}" for n in L7_QUO]
    assert set(names) == set(DENIED_OPERATIONS)
    for name in names:
        connector, _, op = name.partition(":")
        with pytest.raises(DeniedOperation, match="not callable from comms"):
            check_operation(connector, op)
        assert DENIED_OPERATIONS[name]


@pytest.mark.parametrize("spelling", ["unlock_smartlock", "UnlockSmartlock", " unlock-smartlock ",
                                      "hospitable:Cancel_Reservation", "send_bulk_messages",
                                      "quo:startAgentCall"])
def test_spellings_normalize_to_the_denied_operation(spelling):
    assert is_denied(spelling)


def test_only_one_quo_text_to_one_number_is_allowed():
    assert ALLOWED_OPERATIONS == frozenset({"quo:send-message"})
    assert check_operation("quo", "send_message") == "quo:send-message"
    for connector, op in (("quo", "list-contacts"), ("quo", "create-a2p-registration"),
                          ("hospitable", "get-reservations"), ("hospitable", "create-reservation"),
                          ("turno", "anything")):
        with pytest.raises(DeniedOperation, match="deny by default"):
            check_operation(connector, op)


def test_no_comms_module_or_class_exposes_a_denied_operation():
    scanned = 0
    for module in comms_modules():
        assert surface_violations(module) == [], module.__name__
        scanned += 1
    assert scanned >= 8


def test_the_scanner_catches_denied_names():
    class Bad:
        def unlock_door(self): ...
        def send_bulk(self): ...
        def update_reservation_dates(self): ...
        def sendReservationMessage(self): ...
        def start_call(self): ...

    @dataclass
    class BadRequest:
        group_id: str

    found = dict(surface_violations(Bad))
    assert set(found) == {"unlock_door", "send_bulk", "update_reservation_dates",
                          "sendReservationMessage", "start_call"}
    assert surface_violations(BadRequest) == [("group_id", "group")]


def test_no_denied_operation_is_an_attribute_anywhere_in_the_send_path():
    attrs = {n.split(":")[1].replace("-", "_") for n in DENIED_OPERATIONS}
    attrs |= {"lock", "unlock", "cancel", "quote", "send_bulk", "send_group", "start_call",
              "broadcast"}
    for cls in (CrewSendGate, MockCrewTransport, MockOperatorTransport, QuoCliOperatorTransport,
                transport.CrewTransport, transport.OperatorTransport):
        assert not [a for a in attrs if hasattr(cls, a)], cls.__name__


def test_transports_take_exactly_one_recipient():
    t = MockCrewTransport()
    for bad in (["+15555550101", "+15555550102"], ("+15555550101",),
                "+15555550101,+15555550102", "+1 555 555 0101", None):
        with pytest.raises(RecipientError):
            t.send_text(bad, "Hi!")
    assert t.sent == []
    assert t.send_text("+15555550101", "Hi!").status == "sent"


def test_transports_name_their_operation_before_any_call(monkeypatch):
    seen = []
    real = deny_list.check_operation

    def spy(connector, op):
        seen.append((connector, op))
        return real(connector, op)

    monkeypatch.setattr(transport, "check_operation", spy)
    MockCrewTransport().send_text("+15555550101", "Hi!")
    MockOperatorTransport("+15555550199").send("digest")
    QuoCliOperatorTransport("/opt/x/quo.py", "+15555550100", "+15555550199").argv("digest")
    assert seen == [("quo", "send-message")] * 3


# --- class map (package 1b, Appendix B) ---

def test_every_existing_action_key_has_a_class():
    assert set(STAKES) <= set(classes.ACTION_CLASSES)


def test_appendix_b_keys_are_class_d_and_never_run_alone():
    perfect = {"categories": {k: {"decisive_n": 500, "decisive_accuracy": 1.0,
                                  "high_conf_accuracy": 1.0} for k in APPENDIX_B}}
    assert set(APPENDIX_B) == classes.CLASS_D_KEYS
    for key in APPENDIX_B:
        assert classes.class_for(key) == classes.D
        allowed, cls, _ = classes.may_run_without_approval(key, perfect)
        assert not allowed and cls == classes.D
    assert NEVER_EARNED <= classes.CLASS_D_KEYS


def test_an_unknown_key_is_class_d():
    assert classes.class_for("teleport_guest") == classes.D
    assert classes.may_run_without_approval("teleport_guest", {})[0] is False


def test_class_c_never_runs_alone_and_class_b_only_on_its_own_earned_right():
    perfect = {"categories": {k: {"decisive_n": 500, "decisive_accuracy": 1.0,
                                  "high_conf_accuracy": 1.0}
                              for k in ("message_cleaner", "dispatch_vendor", "Act")}}
    for key in ("message_cleaner", "dispatch_vendor", "shift_cleaning", "crew_notice",
                "message_guest"):
        assert classes.class_for(key) == classes.C
        assert classes.may_run_without_approval(key, perfect)[0] is False
    # ask_status does not borrow another key's record.
    assert classes.may_run_without_approval("ask_status", perfect)[0] is False
    earned = {"categories": {"ask_status": {"decisive_n": 30, "decisive_accuracy": 0.99,
                                            "high_conf_accuracy": 0.99}}}
    assert classes.may_run_without_approval("ask_status", earned)[:2] == (True, classes.B)


def test_operator_only_keys_are_class_a():
    for key in ("notify_operator", "auto_close"):
        assert classes.may_run_without_approval(key, {})[:2] == (True, classes.A)


def test_the_class_map_cannot_be_edited_at_runtime():
    with pytest.raises(TypeError):
        classes.ACTION_CLASSES["share_access"] = classes.B
