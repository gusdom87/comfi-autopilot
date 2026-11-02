"""Crew send gate (package 1c, S4-minimal). Every person, number, and
property here is invented; numbers are 555 fixtures. Offline: the only
transport is the mock, and sockets and subprocesses raise if touched.

The default request is a handyman visit check: a vendor is verified by
the directory alone (one entry for the role at the property), so it is
the one recipient that can reach the mock transport. A cleaner text
always waits for the operator (no live Turno assignee read)."""
import json
import os
import socket
import stat
import subprocess
import sys
from dataclasses import fields, replace
from datetime import datetime, timedelta, timezone

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from jev.comms import crew_gate as cg
from jev.comms._files import RepoPathError, append_jsonl
from jev.comms.deny_list import surface_violations
from jev.comms.templates import (DRAFT_TEMPLATES, Template, TemplateRegistry, draft_registry,
                                 lint, load_registry, render)
from jev.comms.transport import (CrewTransport, MockCrewTransport, MockOperatorTransport,
                                 SendResult)

MAPLE, CEDAR, BIRCH, PINE = "own-maple-0001", "own-cedar-0002", "client-birch-0003", "own-pine-0004"
UTC = timezone.utc
DAY = datetime(2026, 11, 2, 15, 0, tzinfo=UTC)                # Mon Nov 2, 9:00 AM CST
NOTICE, ASK = DRAFT_TEMPLATES
HANDY = "+15555550102"

STATUS = Template("cleaner_status_ask_test", "job", "ask_status", ("cleaner",), "en",
                  "Hi! Checking on the clean at {property} on {date}. Still on for {time}? "
                  "Reply YES or NO.")
VISIT = Template("vendor_visit_check_test", "job", "ask_status",
                 ("handyman", "plumber", "electrician"), "en",
                 "Hi! Checking on the visit to {property} on {date}, {start_time} to "
                 "{end_time}. Still on? Reply YES or NO.")
EARNED_STATUS = {"categories": {"ask_status": {
    "decisive_n": 30, "decisive_accuracy": 0.99, "high_conf_accuracy": 0.99}}}
PERFECT = {"categories": {k: {"decisive_n": 500, "decisive_accuracy": 1.0, "high_conf_accuracy": 1.0}
                          for k in ("message_cleaner", "dispatch_vendor", "crew_notice", "ask_status",
                                    "share_access", "charge_guest")}}


@pytest.fixture(autouse=True)
def _offline(monkeypatch):
    def boom(*a, **k):
        raise AssertionError("an offline test touched the network or a subprocess")
    monkeypatch.setattr(socket.socket, "connect", boom)
    monkeypatch.setattr(subprocess, "run", boom)
    monkeypatch.setattr(subprocess, "Popen", boom)


def approve(t: Template) -> Template:
    return replace(t, approved_sha256=t.sha256, approved_by="operator (fixture)",
                   approved_at="2026-10-30")


def registry(*extra) -> TemplateRegistry:
    return TemplateRegistry([approve(NOTICE), approve(ASK), approve(STATUS), approve(VISIT), *extra])


def person(number, name, **over):
    p = {"number": number, "name": name, "language": "en", "notice_sha256": NOTICE.sha256,
         "consent_yes_at": "2026-10-20T15:00:00+00:00", "opted_out": False}
    p.update(over)
    return p


def directory_dict(**people_over):
    people = {
        "w1": person("+15555550101", "Casey"),
        "w2": person(HANDY, "Robin"),
        "w3": person("+15555550103", "Jordan"),
        "w4": person("+15555550104", "Morgan"),
    }
    for pid, over in people_over.items():
        people[pid] = {**people.get(pid, {}), **over}
    return {"schema": "crew-directory/v1",
            "properties": {
                MAPLE: {"short_name": "Maple Street House", "timezone": "America/Chicago",
                        "roles": {"cleaner": ["w1"], "handyman": ["w2"]}},
                CEDAR: {"short_name": "Cedar Hollow", "timezone": "America/Chicago",
                        "roles": {"cleaner": ["w3", "w4"], "plumber": ["w3", "w4"]}},
                BIRCH: {"short_name": "Birch Lane Loft", "timezone": "America/Chicago",
                        "roles": {"handyman": ["w2"]}},
            },
            "people": people}


class FakeReader(cg.ThreadReader):
    def __init__(self, threads=None, raises=None):
        self.threads, self.raises, self.calls = threads or {}, raises, []

    def recent(self, number, since):
        self.calls.append(number)
        if self.raises:
            raise self.raises
        return [m for m in self.threads.get(number, []) if m.at >= since]


def make_gate(tmp_path, *, ledger=None, reg=None, directory=None, reader=None,
              transport=None, own=(MAPLE, CEDAR, PINE), **kw):
    t = transport or MockCrewTransport()
    tmp_path.mkdir(parents=True, exist_ok=True)
    gate = cg.CrewSendGate(
        directory=directory or cg.Directory.from_dict(directory_dict()),
        registry=reg or registry(), own_property_ids=own,
        log_path=str(tmp_path / "crew-gate.jsonl"), transport=t,
        thread_reader=reader or FakeReader(), ledger=ledger, **kw)
    return gate, t


def visit_req(**over):
    base = dict(action_key="ask_status", property_id=MAPLE, role="handyman",
                template_id="vendor_visit_check_test",
                slots={"date": "2026-11-08", "start_time": "09:00", "end_time": "12:00"},
                run_id="run-1", job_id="job-1")
    base.update(over)
    return cg.CrewSendRequest(**base)


def status_req(**over):
    base = dict(action_key="ask_status", property_id=MAPLE, role="cleaner",
                template_id="cleaner_status_ask_test",
                slots={"date": "2026-11-08", "time": "11:00"}, run_id="run-1", job_id="job-1")
    base.update(over)
    return cg.CrewSendRequest(**base)


def ask_req(**over):
    base = dict(action_key="message_cleaner", property_id=MAPLE, role="cleaner",
                template_id="cleaner_late_checkout_ask_v1",
                slots={"date": "2026-11-08", "from_time": "11:00", "to_time": "12:30",
                       "done_by": "15:30"}, run_id="run-2", job_id="job-2")
    base.update(over)
    return cg.CrewSendRequest(**base)


def failed_check(res):
    return [c["check"] for c in res.checks if c["result"] == "fail"]


def holds(res):
    return [c["check"] for c in res.checks if c["result"] == "hold"]


def log(tmp_path):
    path = tmp_path / "crew-gate.jsonl"
    return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []


VISIT_BODY = ("Hi! Checking on the visit to Maple Street House on Sun Nov 8, 9:00 AM to 12:00 PM. "
              "Still on? Reply YES or NO.")


# --- the one path that ends in a (mock) send ---

def test_earned_class_b_vendor_text_goes_through_the_mock_only(tmp_path):
    gate, t = make_gate(tmp_path, ledger=EARNED_STATUS)
    res = gate.submit(visit_req(), DAY)
    assert res.outcome == cg.SENT and res.transport["message_id"] == "mock-1"
    assert t.sent == [(HANDY, VISIT_BODY)]
    assert failed_check(res) == [] and holds(res) == [] and res.action_class == "B"
    assert [r["phase"] for r in log(tmp_path)] == ["intent", "submit"]


def test_at_p1_the_prepared_text_is_held(tmp_path):
    gate, t = make_gate(tmp_path)
    res = gate.submit(visit_req(), DAY)
    assert res.outcome == cg.HELD and t.sent == [] and holds(res) == ["autonomy"]
    assert "no earned right" in res.reasons[0] and res.body == VISIT_BODY


def test_a_cleaner_text_always_waits_for_the_operator_to_confirm_the_recipient(tmp_path):
    gate, t = make_gate(tmp_path, ledger=PERFECT)
    res = gate.submit(status_req(), DAY)
    assert res.outcome == cg.HELD and t.sent == [] and holds(res) == ["recipient_check"]
    assert "no live Turno assignee read" in res.reasons[0]
    assert res.recipient_id == "w1" and failed_check(res) == []


def test_class_c_waits_even_on_a_perfect_ledger(tmp_path):
    gate, t = make_gate(tmp_path, ledger=PERFECT)
    res = gate.submit(ask_req(), DAY)
    assert res.outcome == cg.HELD and t.sent == [] and res.action_class == "C"
    assert "autonomy" in holds(res)
    assert res.body == ("Hi! Checkout at Maple Street House on Sun Nov 8 may move from 11:00 AM "
                        "to 12:30 PM. Can you start at 12:30 PM and still be done by 3:30 PM? "
                        "Reply YES or NO.")


# --- recipient ---

def test_unknown_recipient_is_refused(tmp_path):
    gate, t = make_gate(tmp_path, ledger=EARNED_STATUS)
    no_entry = gate.submit(visit_req(role="electrician"), DAY)
    assert no_entry.outcome == cg.REFUSED and failed_check(no_entry) == ["recipient"]
    assert "no verified electrician" in no_entry.reasons[0]
    stranger = gate.submit(visit_req(recipient_id="w-nobody"), DAY)
    assert stranger.outcome == cg.REFUSED and failed_check(stranger) == ["recipient"]
    wrong_person = gate.submit(visit_req(recipient_id="w1"), DAY)
    assert wrong_person.outcome == cg.REFUSED
    assert "not the verified handyman" in wrong_person.reasons[0]
    assert t.sent == []


def test_a_matching_recipient_from_the_run_record_passes(tmp_path):
    gate, _ = make_gate(tmp_path, ledger=EARNED_STATUS)
    assert gate.submit(visit_req(recipient_id="w2"), DAY).outcome == cg.SENT


def test_two_entries_for_a_role_is_refused_not_guessed(tmp_path):
    gate, t = make_gate(tmp_path, ledger=EARNED_STATUS)
    res = gate.submit(visit_req(property_id=CEDAR, role="plumber"), DAY)
    assert res.outcome == cg.REFUSED and "2 active plumber entries" in res.reasons[0]
    inactive = cg.Directory.from_dict(directory_dict(w4={"active": False}))
    gate2, t2 = make_gate(tmp_path / "b", ledger=EARNED_STATUS, directory=inactive)
    assert gate2.submit(visit_req(property_id=CEDAR, role="plumber"), DAY).outcome == cg.SENT
    assert t2.sent[0][0] == "+15555550103" and t.sent == []


def test_client_property_is_refused(tmp_path):
    gate, t = make_gate(tmp_path, ledger=EARNED_STATUS)
    res = gate.submit(visit_req(property_id=BIRCH), DAY)
    assert failed_check(res) == ["allowlist"] and t.sent == []


def test_own_property_missing_from_the_directory_is_refused(tmp_path):
    gate, _ = make_gate(tmp_path, ledger=EARNED_STATUS)
    assert failed_check(gate.submit(visit_req(property_id=PINE), DAY)) == ["directory"]


def test_unknown_role_is_refused(tmp_path):
    gate, _ = make_gate(tmp_path, ledger=EARNED_STATUS)
    assert failed_check(gate.submit(visit_req(role="operator"), DAY)) == ["role"]


def test_language_must_match(tmp_path):
    spanish = cg.Directory.from_dict(directory_dict(w2={"language": "es"}))
    gate, _ = make_gate(tmp_path, ledger=EARNED_STATUS, directory=spanish)
    res = gate.submit(visit_req(), DAY)
    assert failed_check(res) == ["language"] and "translation" in res.reasons[0]


# --- template-only bodies ---

def test_a_request_has_no_body_field():
    names = {f.name for f in fields(cg.CrewSendRequest)}
    assert not names & {"body", "text", "content", "message", "draft"}
    with pytest.raises(TypeError):
        cg.CrewSendRequest(body="free text", **{k: "x" for k in names - {"recipient_id"}})


def test_unapproved_template_is_refused(tmp_path):
    assert all(not t.approved for t in DRAFT_TEMPLATES)
    gate, t = make_gate(tmp_path, ledger=PERFECT, reg=draft_registry())
    res = gate.submit(ask_req(), DAY)
    assert failed_check(res) == ["template"] and "not approved" in res.reasons[0]
    assert t.sent == []


def test_template_text_changed_after_approval_is_refused(tmp_path):
    edited = replace(approve(VISIT), text=VISIT.text.replace("Still on?", "Are we still on?"))
    reg = TemplateRegistry([approve(NOTICE), edited])
    gate, _ = make_gate(tmp_path, ledger=EARNED_STATUS, reg=reg)
    res = gate.submit(visit_req(), DAY)
    assert failed_check(res) == ["template"] and "changed after approval" in res.reasons[0]


def test_unknown_template_is_refused(tmp_path):
    gate, _ = make_gate(tmp_path, ledger=EARNED_STATUS)
    res = gate.submit(visit_req(template_id="made_up_v9"), DAY)
    assert failed_check(res) == ["template"]
    assert "not in the approved-template registry" in res.reasons[0]


def test_template_must_fit_the_action_and_role(tmp_path):
    gate, _ = make_gate(tmp_path, ledger=PERFECT)
    wrong_key = gate.submit(visit_req(action_key="dispatch_vendor"), DAY)
    assert "is for ask_status, not dispatch_vendor" in wrong_key.reasons[0]
    wrong_role = gate.submit(visit_req(role="cleaner"), DAY)
    assert "not for the cleaner role" in wrong_role.reasons[0]


@pytest.mark.parametrize("slots,needle", [
    ({"date": "2026-11-08", "start_time": "tell the handyman to come at 6", "end_time": "12:00"},
     "not a 24-hour HH:MM"),
    ({"date": "Sunday", "start_time": "09:00", "end_time": "12:00"}, "not an ISO date"),
    ({"date": "2026-11-08", "start_time": "09:00"}, "missing: end_time"),
    ({"date": "2026-11-08", "start_time": "09:00", "end_time": "12:00", "note": "bring a ladder"},
     "not in the template: note"),
    ({"date": "2026-11-08", "start_time": "09:00", "end_time": "12:00", "property": "Robin's place"},
     "not in the template: property"),
    ({"date": "2026-11-08", "start_time": 9, "end_time": "12:00"}, "a string is required"),
])
def test_slots_are_dates_and_times_only(tmp_path, slots, needle):
    gate, t = make_gate(tmp_path, ledger=EARNED_STATUS)
    res = gate.submit(visit_req(slots=slots), DAY)
    assert failed_check(res) == ["slots"] and needle in res.reasons[0] and t.sent == []


def test_a_free_text_slot_is_rejected_at_load(tmp_path):
    loose = approve(Template("loose_v1", "job", "ask_status", ("handyman",), "en",
                             "Hi! {note} Reply YES or NO."))
    reg = registry(loose)
    assert "loose_v1" in reg.rejected and reg.get("loose_v1") is None
    gate, _ = make_gate(tmp_path, ledger=EARNED_STATUS, reg=reg)
    res = gate.submit(visit_req(template_id="loose_v1", slots={"note": "x"}), DAY)
    assert "rejected at load" in res.reasons[0]


def test_render_refuses_a_slot_with_no_type_even_past_the_registry():
    # Second line behind the load check (scripts/mutation_check_comms.py).
    loose = Template("loose_v2", "job", "ask_status", ("handyman",), "en", "Hi! {note} Reply YES or NO.")
    with pytest.raises(ValueError, match="not a slot the caller may fill"):
        render(loose, "Maple Street House", {"note": "tell the handyman to come at 6"})


@pytest.mark.parametrize("text,hit", [
    ("Hi! She finished early at {property}. Reply YES or NO.", "gendered pronoun"),
    ("Hi! Get him moving at {property}.", "gendered pronoun"),
    ("Hi! The clean at {property} pays $90 today.", "money"),
    ("Hi! Your rate at {property} goes up.", "money"),
    ("Hi! The door code at {property} is on the card.", "access"),
    ("Hi! Use 4821 at {property}.", "long digit run"),
    ("Hi! The camera at {property} saw nobody.", "camera"),
    ("Hi! Another no-show at {property}.", "discipline or performance"),
    ("Hi! See you at {property}. - Gus", "signed as the operator"),
    ("Hi! The clean at {property} — still on?", "dash"),
])
def test_content_lint_rejects_templates(text, hit):
    reg = TemplateRegistry([approve(Template("bad_v1", "job", "ask_status", ("cleaner",), "en", text))])
    assert reg.get("bad_v1") is None
    assert hit in reg.rejected["bad_v1"][0]


def test_the_notice_may_disclose_cameras_and_a_job_text_may_not():
    assert lint(NOTICE.text, "notice") == []
    assert "camera" in lint(NOTICE.text, "job")


def test_shipped_drafts_load_clean_and_unapproved():
    reg = draft_registry()
    assert reg.rejected == {} and reg.ids() == ["cleaner_late_checkout_ask_v1", "crew_notice_v1"]
    assert not any(reg.get(i).approved for i in reg.ids())


def test_a_worker_name_in_the_rendered_text_is_refused(tmp_path):
    data = directory_dict()
    data["properties"][MAPLE]["short_name"] = "Casey's Place"
    gate, t = make_gate(tmp_path, ledger=EARNED_STATUS, directory=cg.Directory.from_dict(data))
    res = gate.submit(visit_req(), DAY)
    assert failed_check(res) == ["content"] and "worker name" in res.reasons[0] and t.sent == []


def test_the_voice_lint_hook_can_refuse(tmp_path):
    gate, _ = make_gate(tmp_path, ledger=EARNED_STATUS, voice_lint=lambda body: ["stiff opener"])
    res = gate.submit(visit_req(), DAY)
    assert failed_check(res) == ["content"] and "voice: stiff opener" in res.reasons[0]


def test_registry_file_round_trip(tmp_path):
    path = tmp_path / "templates.json"
    path.write_text(json.dumps({"schema": "crew-templates/v1", "templates": [
        {"template_id": t.template_id, "kind": t.kind, "action_key": t.action_key,
         "roles": list(t.roles), "language": t.language, "text": t.text,
         "approved_sha256": t.sha256, "approved_by": "operator (fixture)"}
        for t in (NOTICE, VISIT)]}))
    reg = load_registry(str(path))
    assert reg.get("crew_notice_v1").approved and reg.get("vendor_visit_check_test").approved


# --- consent ---

def test_consent_is_required_for_a_job_text(tmp_path):
    cases = (({"consent_yes_at": ""}, "no recorded YES"),
             ({"notice_sha256": "0" * 64}, "no recorded YES"),
             ({"opted_out": True}, "opted out"),
             ({"opted_out": None}, "opted out"))
    for n, (over, needle) in enumerate(cases):
        d = cg.Directory.from_dict(directory_dict(w2=over))
        gate, t = make_gate(tmp_path / f"case{n}", ledger=EARNED_STATUS, directory=d)
        res = gate.submit(visit_req(), DAY)
        assert failed_check(res) == ["consent"] and needle in res.reasons[0] and t.sent == []


def test_a_missing_opt_out_flag_reads_as_opted_out(tmp_path):
    data = directory_dict()
    del data["people"]["w2"]["opted_out"]
    gate, _ = make_gate(tmp_path, ledger=EARNED_STATUS, directory=cg.Directory.from_dict(data))
    assert failed_check(gate.submit(visit_req(), DAY)) == ["consent"]


def test_no_job_text_before_the_worker_notice_is_approved(tmp_path):
    reg = TemplateRegistry([NOTICE, approve(VISIT)])
    gate, _ = make_gate(tmp_path, ledger=EARNED_STATUS, reg=reg)
    res = gate.submit(visit_req(), DAY)
    assert failed_check(res) == ["consent"] and "worker notice is not approved" in res.reasons[0]


def test_the_notice_itself_needs_no_prior_yes_but_waits_for_the_operator(tmp_path):
    d = cg.Directory.from_dict(directory_dict(w2={"consent_yes_at": "", "notice_sha256": ""}))
    gate, t = make_gate(tmp_path, ledger=PERFECT, directory=d)
    res = gate.submit(cg.CrewSendRequest("crew_notice", MAPLE, "handyman", "crew_notice_v1", {},
                                         "run-n", "notice-w2"), DAY)
    assert res.outcome == cg.HELD and res.action_class == "C" and t.sent == []
    assert res.body == NOTICE.text


# Fable audit probe C17: an approved notice-kind template on a Class B
# key, with camera wording. It used to skip the recorded YES and the
# camera lint and reach the mock transport.
C17 = Template("c17_status_notice_test", "notice", "ask_status", ("handyman",), "en",
               "Hi! Checking on the visit to {property} on {date}, {start_time} to {end_time}. "
               "The cameras recorded the last visit. Still on? Reply YES or NO.")
NO_YES = {"consent_yes_at": "", "notice_sha256": ""}


def test_a_notice_kind_template_on_another_key_is_rejected_at_load(tmp_path):
    reg = registry(approve(C17))
    assert reg.get(C17.template_id) is None
    assert reg.rejected[C17.template_id] == ["a notice template carries crew_notice, not 'ask_status'"]
    no_yes = cg.Directory.from_dict(directory_dict(w2=NO_YES))
    gate, t = make_gate(tmp_path, ledger=EARNED_STATUS, reg=reg, directory=no_yes)
    res = gate.submit(visit_req(template_id=C17.template_id), DAY)
    assert failed_check(res) == ["template"] and "rejected at load" in res.reasons[0]
    assert t.sent == []


def test_a_job_template_on_the_notice_key_is_rejected_at_load():
    job = approve(Template("job_on_notice_key_test", "job", "crew_notice", ("handyman",), "en",
                           "Hi! Reply YES to get scheduling texts from Comfi Stays."))
    reg = registry(job)
    assert reg.get(job.template_id) is None
    assert reg.rejected[job.template_id] == ["a job template can't carry crew_notice"]


def test_only_the_registered_notice_goes_without_a_recorded_yes(tmp_path):
    # Fable audit F3(b): consent is keyed on the registered notice's ID.
    no_yes = cg.Directory.from_dict(directory_dict(w2=NO_YES))
    v2 = approve(replace(NOTICE, template_id="crew_notice_v2", text=NOTICE.text.replace("Hi!", "Hello!")))
    gate, t = make_gate(tmp_path, ledger=PERFECT, reg=registry(v2), directory=no_yes)
    second = gate.submit(cg.CrewSendRequest("crew_notice", MAPLE, "handyman", "crew_notice_v2", {},
                                            "run-n", "notice-w2"), DAY)
    assert failed_check(second) == ["consent"] and "no recorded YES" in second.reasons[0]
    registered = gate.submit(cg.CrewSendRequest("crew_notice", MAPLE, "handyman", "crew_notice_v1",
                                                {}, "run-n", "notice-w2"), DAY)
    assert failed_check(registered) == [] and registered.outcome == cg.HELD
    # A job template named as the gate's notice still needs the YES.
    gate2, t2 = make_gate(tmp_path / "b", ledger=EARNED_STATUS, directory=no_yes,
                          notice_template_id=VISIT.template_id)
    job = gate2.submit(visit_req(), DAY)
    assert failed_check(job) == ["consent"] and "no recorded YES" in job.reasons[0]
    assert t.sent == [] and t2.sent == []


# --- deny list and autonomy ---

@pytest.mark.parametrize("key", ["unlock_smartlock", "lock-smartlock", "cancel_reservation",
                                 "create_quote", "send-bulk-messages", "send_group_message",
                                 "start_agent_call", "ask_knowledge_agent"])
def test_deny_listed_operations_are_refused(tmp_path, key):
    gate, t = make_gate(tmp_path, ledger=PERFECT)
    res = gate.submit(visit_req(action_key=key), DAY)
    assert failed_check(res) == ["operation"] and "deny-listed" in res.reasons[0] and t.sent == []


@pytest.mark.parametrize("key", ["share_access", "charge_guest", "approve_spend", "message_guest",
                                 "notify_operator", "made_up_key"])
def test_non_crew_keys_are_refused(tmp_path, key):
    gate, t = make_gate(tmp_path, ledger=PERFECT)
    res = gate.submit(visit_req(action_key=key), DAY)
    assert failed_check(res) == ["operation"] and "not a crew text key" in res.reasons[0]


def test_the_gate_surface_is_evaluate_and_submit_only():
    public = {n for n in dir(cg.CrewSendGate) if not n.startswith("_")}
    assert public == {"evaluate", "submit"}
    assert surface_violations(cg.CrewSendGate) == []
    assert surface_violations(cg) == []


def test_only_an_offline_crew_transport_is_accepted(tmp_path):
    class LiveCrew(CrewTransport):
        def send_text(self, to, body):
            raise AssertionError("never reached")

    with pytest.raises(ValueError, match="no live crew transport"):
        make_gate(tmp_path, transport=LiveCrew())
    with pytest.raises(TypeError):
        make_gate(tmp_path, transport=MockOperatorTransport("+15555550199"))


# --- thread re-read: yield, loop guard, read failure ---

def test_a_failed_thread_read_refuses(tmp_path):
    gate, t = make_gate(tmp_path, ledger=EARNED_STATUS, reader=FakeReader(raises=OSError("down")))
    res = gate.submit(visit_req(), DAY)
    assert failed_check(res) == ["thread"] and t.sent == []


def test_yield_to_a_manual_text_in_the_last_two_hours(tmp_path):
    recent = FakeReader({HANDY: [cg.ThreadMessage(DAY - timedelta(minutes=90), "out")]})
    gate, t = make_gate(tmp_path, ledger=EARNED_STATUS, reader=recent)
    res = gate.submit(visit_req(), DAY)
    assert res.outcome == cg.HELD and holds(res) == ["yield"] and t.sent == []
    older = FakeReader({HANDY: [cg.ThreadMessage(DAY - timedelta(hours=3), "out")]})
    gate2, _ = make_gate(tmp_path / "b", ledger=EARNED_STATUS, reader=older)
    assert gate2.submit(visit_req(), DAY).outcome == cg.SENT


def test_an_automated_outbound_text_does_not_count_as_a_human(tmp_path):
    auto = FakeReader({HANDY: [cg.ThreadMessage(DAY - timedelta(minutes=10), "out", automated=True)]})
    gate, _ = make_gate(tmp_path, ledger=EARNED_STATUS, reader=auto)
    assert gate.submit(visit_req(), DAY).outcome == cg.SENT


def test_loop_guard_pauses_after_two_other_texts(tmp_path):
    msgs = [cg.ThreadMessage(DAY - timedelta(hours=5), "out", automated=True),
            cg.ThreadMessage(DAY - timedelta(hours=4), "in", kind="other"),
            cg.ThreadMessage(DAY - timedelta(hours=3), "in", kind="other")]
    gate, t = make_gate(tmp_path, ledger=EARNED_STATUS, reader=FakeReader({HANDY: msgs}))
    res = gate.submit(visit_req(), DAY)
    assert res.outcome == cg.HELD and holds(res) == ["loop_guard"] and t.sent == []


# --- caps, quiet hours, duplicates ---

def _seed(tmp_path, n, at, *, recipient="w2", job=None):
    tmp_path.mkdir(parents=True, exist_ok=True)
    for i in range(n):
        append_jsonl(str(tmp_path / "crew-gate.jsonl"), {
            "at": at.isoformat(), "phase": "submit", "outcome": "sent",
            "recipient_id": recipient, "job_id": job or f"seed-job-{recipient}-{i}",
            "idempotency_key": f"seed-{recipient}-{job}-{i}"})


def test_cap_per_worker_per_day(tmp_path):
    _seed(tmp_path, 3, DAY - timedelta(hours=1))
    gate, _ = make_gate(tmp_path, ledger=EARNED_STATUS)
    res = gate.submit(visit_req(), DAY)
    assert res.outcome == cg.HELD and "3 automated texts to this worker today" in " ".join(res.reasons)


def test_cap_per_job(tmp_path):
    _seed(tmp_path, 3, DAY - timedelta(days=2), job="job-1")
    gate, _ = make_gate(tmp_path, ledger=EARNED_STATUS)
    res = gate.submit(visit_req(), DAY)
    assert res.outcome == cg.HELD and "3 automated texts on this job" in " ".join(res.reasons)


def test_cap_across_all_workers(tmp_path):
    for n in range(10):
        _seed(tmp_path, 1, DAY - timedelta(hours=2), recipient=f"other-{n}")
    gate, _ = make_gate(tmp_path, ledger=EARNED_STATUS)
    res = gate.submit(visit_req(), DAY)
    assert res.outcome == cg.HELD and "across all workers today" in " ".join(res.reasons)


def test_the_cap_day_is_property_local(tmp_path):
    # 04:30 UTC on Nov 2 is 10:30 PM CST on Nov 1: yesterday, locally.
    _seed(tmp_path, 3, datetime(2026, 11, 2, 4, 30, tzinfo=UTC))
    gate, _ = make_gate(tmp_path, ledger=EARNED_STATUS)
    assert gate.submit(visit_req(), DAY).outcome == cg.SENT


@pytest.mark.parametrize("when,expected,until", [
    (datetime(2026, 11, 1, 6, 30, tzinfo=UTC), cg.DEFERRED, "2026-11-01T07:00:00-06:00"),  # 1:30 AM CDT
    (datetime(2026, 11, 1, 7, 30, tzinfo=UTC), cg.DEFERRED, "2026-11-01T07:00:00-06:00"),  # 1:30 AM CST
    (datetime(2026, 11, 1, 12, 59, tzinfo=UTC), cg.DEFERRED, "2026-11-01T07:00:00-06:00"),  # 6:59 AM CST
    (datetime(2026, 11, 1, 13, 1, tzinfo=UTC), cg.SENT, ""),                                # 7:01 AM CST
    (datetime(2026, 11, 3, 2, 59, tzinfo=UTC), cg.SENT, ""),                                # 8:59 PM CST
    (datetime(2026, 11, 3, 3, 0, tzinfo=UTC), cg.DEFERRED, "2026-11-03T07:00:00-06:00"),   # 9:00 PM CST
    (datetime(2026, 10, 31, 2, 30, tzinfo=UTC), cg.DEFERRED, "2026-10-31T07:00:00-05:00"),  # 9:30 PM CDT
])
def test_worker_quiet_hours_across_the_dst_change(tmp_path, when, expected, until):
    gate, t = make_gate(tmp_path, ledger=EARNED_STATUS)
    res = gate.submit(visit_req(), when)
    assert res.outcome == expected and res.deferred_until == until
    assert len(t.sent) == (1 if expected == cg.SENT else 0)


def test_one_attempt_per_run_recipient_and_template(tmp_path):
    gate, t = make_gate(tmp_path, ledger=EARNED_STATUS)
    assert gate.submit(visit_req(), DAY).outcome == cg.SENT
    again = gate.submit(visit_req(), DAY + timedelta(minutes=5))
    assert again.outcome == cg.REFUSED and failed_check(again) == ["duplicate"]
    assert gate.submit(visit_req(run_id="run-other"), DAY + timedelta(minutes=6)).outcome == cg.SENT
    assert len(t.sent) == 2


def test_an_unclear_send_is_never_resent(tmp_path):
    gate, t = make_gate(tmp_path, ledger=EARNED_STATUS, transport=MockCrewTransport(raises=TimeoutError()))
    res = gate.submit(visit_req(), DAY)
    assert res.outcome == cg.UNKNOWN and "never re-sent" in res.reasons[-1]
    again = gate.submit(visit_req(), DAY + timedelta(minutes=1))
    assert failed_check(again) == ["duplicate"]


def test_a_failed_send_is_logged_and_not_resent(tmp_path):
    gate, _ = make_gate(tmp_path, ledger=EARNED_STATUS,
                        transport=MockCrewTransport(results=[SendResult("failed", None, "HTTP 400")]))
    assert gate.submit(visit_req(), DAY).outcome == cg.SEND_FAILED
    assert failed_check(gate.submit(visit_req(), DAY)) == ["duplicate"]


# --- logging ---

def test_every_evaluation_and_submit_is_logged_in_full(tmp_path):
    gate, _ = make_gate(tmp_path, ledger=EARNED_STATUS)
    gate.evaluate(visit_req(), DAY)
    gate.submit(visit_req(property_id=BIRCH), DAY)
    gate.submit(visit_req(), DAY)
    rows = log(tmp_path)
    assert [(r["phase"], r["outcome"]) for r in rows] == [
        ("evaluate", "ready"), ("submit", "refused"), ("intent", "sending"), ("submit", "sent")]
    full = rows[-1]
    for key in ("checks", "request", "template_sha256", "body", "body_sha256", "action_class",
                "right", "idempotency_key", "transport"):
        assert full[key]
    assert full["recipient_id"] == "w2" and full["recipient_number_last4"] == "0102"
    assert full["template_sha256"] == VISIT.sha256
    path = tmp_path / "crew-gate.jsonl"
    assert stat.S_IMODE(os.stat(path).st_mode) == 0o600
    assert HANDY not in path.read_text()


def test_evaluate_never_sends(tmp_path):
    gate, t = make_gate(tmp_path, ledger=EARNED_STATUS)
    assert gate.evaluate(visit_req(), DAY).outcome == cg.READY and t.sent == []


def test_a_log_inside_the_repo_is_refused(tmp_path):
    with pytest.raises(RepoPathError):
        cg.CrewSendGate(directory=cg.Directory.from_dict(directory_dict()), registry=registry(),
                        own_property_ids=[MAPLE],
                        log_path=os.path.join(os.path.dirname(__file__), "x.jsonl"),
                        transport=MockCrewTransport(), thread_reader=FakeReader())


def test_naive_time_is_rejected(tmp_path):
    gate, _ = make_gate(tmp_path)
    with pytest.raises(ValueError):
        gate.evaluate(visit_req(), datetime(2026, 11, 2, 9, 0))


# --- directory validation ---

@pytest.mark.parametrize("mutate,needle", [
    (lambda d: d["people"]["w2"].update(number="+15555550101"), "share a number"),
    (lambda d: d["people"]["w2"].update(number="555-0102"), "no valid E.164"),
    (lambda d: d["properties"][MAPLE]["roles"].update(locksmith=["w1"]), "unknown role"),
    (lambda d: d["properties"][MAPLE]["roles"].update(cleaner=["w9"]), "unknown people"),
])
def test_directory_inconsistencies_fail_at_load(mutate, needle):
    data = directory_dict()
    mutate(data)
    with pytest.raises(ValueError, match=needle):
        cg.Directory.from_dict(data)


def test_directory_file_load(tmp_path):
    path = tmp_path / "crew.json"
    path.write_text(json.dumps(directory_dict()))
    d = cg.Directory.load(str(path))
    assert d.resolve(MAPLE, "cleaner")[0].person_id == "w1"
    assert HANDY in d.numbers()
