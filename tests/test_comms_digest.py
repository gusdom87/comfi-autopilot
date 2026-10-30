"""Operator digest (package S1, floor 1d) on a fixture run log. Every row
in tests/fixtures/comms is invented. Offline: a socket connect or a
subprocess raises if anything touches one."""
import hashlib
import json
import os
import socket
import stat
import subprocess
import sys
from collections import Counter
from datetime import datetime, timedelta, timezone

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from jev.change_guard import HOLDING_REPLY
from jev.comms import digest as dg
from jev.comms._files import RepoPathError
from jev.comms.transport import (FAILED, MockOperatorTransport, QuoCliOperatorTransport,
                                 RecipientError)

FIX = os.path.join(os.path.dirname(__file__), "fixtures", "comms")
SHADOW = os.path.join(FIX, "shadow-ledger.jsonl")
ACTUATOR = os.path.join(FIX, "actuator")
MAPLE, CEDAR, BIRCH = "own-maple-0001", "own-cedar-0002", "client-birch-0003"
NOW = datetime(2026, 10, 31, 1, 0, tzinfo=timezone.utc)      # Fri Oct 30, 8:00 PM CDT
WATERMARK = "2026-10-30T12:00:00+00:00"                        # Fri Oct 30, 7:00 AM CDT
OPERATOR, LINE = "+15555550199", "+15555550100"


@pytest.fixture(autouse=True)
def _offline(monkeypatch):
    def boom(*a, **k):
        raise AssertionError("an offline test touched the network or a subprocess")
    monkeypatch.setattr(socket.socket, "connect", boom)
    monkeypatch.setattr(subprocess, "run", boom)
    monkeypatch.setattr(subprocess, "Popen", boom)


def raw_cfg(tmp_path, **over):
    d = {"schema": dg.CONFIG_SCHEMA, "own_property_ids": [MAPLE, CEDAR],
         "shadow_ledger": SHADOW, "actuator_dir": ACTUATOR,
         "out_dir": str(tmp_path / "out"), "timezone": "America/Chicago",
         "operator_number": None, "from_number": None, "a2p_confirmed": False}
    d.update(over)
    return d


def make_cfg(tmp_path, **over):
    return dg.config_from_dict(raw_cfg(tmp_path, **over))


def sendable_cfg(tmp_path, **over):
    cli = tmp_path / "quo.py"
    cli.write_text("# stand-in path; never executed\n")
    base = dict(operator_number=OPERATOR, from_number=LINE, a2p_confirmed=True, quo_cli=str(cli))
    base.update(over)
    return make_cfg(tmp_path, **base)


def compose(cfg, watermark=WATERMARK, now=NOW):
    return dg.compose(cfg, dg.read_sources(cfg), {"watermark_utc": watermark}, now)


def no_factory(cfg):
    raise AssertionError("the transport was built although a gate should have refused")


def log_rows(cfg):
    with open(os.path.join(cfg.out_dir, "digest-log.jsonl")) as fh:
        return [json.loads(line) for line in fh]


# --- counts and sections ---

def test_counts_on_the_fixture(tmp_path):
    d = compose(make_cfg(tmp_path))
    assert len(d.new) == 9                       # 6 shadow + 3 triaged actuator rows
    assert Counter(i.category for i in d.new) == {"Act": 4, "Confirm": 4, "Ignore": 1}
    assert sum(i.failsafe for i in d.new) == 1
    assert d.skipped_before_triage == {"thread_position": 1}
    assert len(d.waiting) == 5
    assert d.waiting_old == 2                    # one shadow row, one queue row, both past 24 h
    assert len(d.sends) == 2
    assert len(d.disarms) == 1
    assert [e["event"] for _, e in d.lane_events] == ["arm"]   # the 10:00Z disarm is before the window
    assert len(d.trips) == 4
    assert d.left_out_client == 2 and d.left_out_no_id == 1
    assert d.unreadable == 1                     # the fixture's one broken line
    glance = d.text.split("WAITING ON YOU")[0]
    for line in ("Waiting on you: 5", "New decisions: 9 (Act 4, Confirm 4, Ignore 1; 1 fail-safe)",
                 "Sends: 2 (1 confirmed)", "Auto-disarms: 1", "Guard trips: 4"):
        assert line in glance


def test_window_excludes_rows_before_the_last_digest(tmp_path):
    d = compose(make_cfg(tmp_path))
    assert "pure thank-you" not in d.text        # s01, 09:00Z, before the 12:00Z watermark
    assert all(i.at > datetime.fromisoformat(WATERMARK) for i in d.new)


def test_waiting_items_carry_their_drafts_act_first(tmp_path):
    d = compose(make_cfg(tmp_path))
    order = [(i.source, i.reservation_id) for i in d.waiting]
    assert order == [("shadow", "res-s02"), ("actuator", "res-a2"), ("shadow", "res-s03"),
                     ("shadow", "res-s06"), ("shadow", "res-s07")]
    waiting = d.text.split("WAITING ON YOU")[1].split("NEW DECISIONS")[0]
    for draft in ("Hi! The wifi details are on the card by the TV.", "Sure, stay until 1!",
                  HOLDING_REPLY, "Sorry about that! Let me look into it."):
        assert f'Draft: "{draft}"' in waiting
    assert "Draft: none written" in waiting      # the fail-safe row
    assert "Where do we park?" not in waiting    # answered by the host: not waiting
    assert "2 held items older than 24 hours" in waiting


def test_a_message_in_both_logs_waits_once_as_the_actuator_copy(tmp_path):
    ledger = tmp_path / "shadow.jsonl"
    with open(SHADOW) as fh:
        lines = fh.read()
    twin = {"decided_at": "2026-10-30T19:25:00+00:00", "property": "Cedar Hollow",
            "property_id": CEDAR, "reservation_id": "res-a2", "message_at": "2026-10-30T19:20:00Z",
            "message_text": "Can we stay until 1pm?", "engine_category": "Confirm",
            "confidence": 0.9, "draft": HOLDING_REPLY, "reason": "shadow copy",
            "failsafe": False, "answered_by_human": False, "host_outcome": ""}
    ledger.write_text(lines + json.dumps(twin) + "\n")
    d = compose(make_cfg(tmp_path, shadow_ledger=str(ledger)))
    same = [i for i in d.waiting if i.reservation_id == "res-a2"]
    assert len(same) == 1 and same[0].source == "actuator"


def test_guard_trips_surface_with_plain_labels(tmp_path):
    d = compose(make_cfg(tmp_path))
    labels = {(i.reservation_id, label) for i, label in d.trips}
    assert ("res-s03", "Phase 0 guard held it (stay change: late checkout)") in labels
    assert ("res-s07", "never-graduate topic: damage") in labels
    assert any(r == "res-s06" and lab.startswith("fail-safe") for r, lab in labels)
    assert any(r == "res-a2" and lab.startswith("deny list hit on an Act draft")
               and lab.endswith("grants late checkout") for r, lab in labels)


def test_auto_disarm_and_lane_changes_surface(tmp_path):
    d = compose(make_cfg(tmp_path))
    section = d.text.split("AUTO-DISARMS")[1].split("GUARD TRIPS")[0]
    assert "deny-list hit on an Act draft: grants late checkout (trigger: deny_list)" in section
    assert "Lane changes:" in section and "arm: operator's words, fixture" in section
    assert "operator --disarm" not in section    # before the window
    assert "Auto-disarm" in d.sms


def test_sends_section_reports_every_attempt(tmp_path):
    d = compose(make_cfg(tmp_path))
    section = d.text.split("SENDS (2)")[1].split("AUTO-DISARMS")[0]
    assert "Sent (message ID msg-001)" in section
    assert "Status unknown; never retried" in section
    assert "Sends: 2 (1 unconfirmed)" in d.sms


# --- own listings only ---

def test_client_rows_are_never_mixed_in_by_default(tmp_path):
    cfg = make_cfg(tmp_path)
    res = dg.run(cfg, NOW)
    with open(res.payload_path) as fh:
        payload = fh.read()
    for text in (res.digest.text, res.digest.sms, payload):
        assert "CLIENT-ONLY-MARKER" not in text
        assert "Birch Lane Loft" not in text
    assert "2 decisions on client listings (own listings only, by config)" in res.digest.text
    assert "CLIENT LISTINGS" not in res.digest.text


def test_client_section_only_when_the_config_says_so(tmp_path):
    base = compose(make_cfg(tmp_path))
    d = compose(make_cfg(tmp_path, include_client_properties=True))
    assert (len(d.new), len(d.waiting), len(d.trips), len(d.sends)) == \
        (len(base.new), len(base.waiting), len(base.trips), len(base.sends))
    head, _, client = d.text.partition("CLIENT LISTINGS")
    assert "CLIENT-ONLY-MARKER" not in head
    assert "CLIENT-ONLY-MARKER message" in client and "CLIENT-ONLY-MARKER draft" in client
    assert "CLIENT-ONLY-MARKER" not in d.sms
    assert len(d.client_new) == 2 and len(d.client_waiting) == 1


def test_an_actuator_queue_row_on_a_client_listing_is_not_mixed_in(tmp_path):
    # The actuator never reads client listings; if a row ever claims one,
    # the digest still keeps it out of the own-listing sections.
    act = tmp_path / "actuator"
    act.mkdir()
    (act / "confirm-queue.jsonl").write_text(json.dumps({
        "queued_at": "2026-10-30T19:00:00+00:00", "kind": "confirm", "hold_reason": "held_confirm",
        "property": "Birch Lane Loft", "property_id": BIRCH, "reservation_id": "res-cq",
        "message_at": "2026-10-30T18:55:00+00:00", "message_text": "CLIENT-QUEUE-MARKER",
        "engine_category": "Confirm", "confidence": 0.8, "draft": "CLIENT-QUEUE-MARKER draft",
        "engine_reason": "x", "failsafe": False}) + "\n")
    d = compose(make_cfg(tmp_path, actuator_dir=str(act)))
    assert all(i.property_id in (MAPLE, CEDAR) for i in d.waiting)
    assert "CLIENT-QUEUE-MARKER" not in d.text and "CLIENT-QUEUE-MARKER" not in d.sms
    shown = compose(make_cfg(tmp_path, actuator_dir=str(act), include_client_properties=True))
    assert "CLIENT-QUEUE-MARKER" in shown.text.partition("CLIENT LISTINGS")[2]
    assert all(i.property_id in (MAPLE, CEDAR) for i in shown.waiting)


def test_rows_with_no_property_id_are_left_out(tmp_path):
    d = compose(make_cfg(tmp_path, include_client_properties=True))
    assert "NO-ID-MARKER" not in d.text
    assert "1 decision with no property ID" in d.text


def test_listings_match_by_id_never_by_name(tmp_path):
    ledger = tmp_path / "shadow.jsonl"
    impostor = {"decided_at": "2026-10-30T15:00:00+00:00", "property": "Maple Street House",
                "property_id": "client-lookalike-9", "reservation_id": "res-x", "message_at": "",
                "message_text": "IMPOSTOR-MARKER", "engine_category": "Act", "confidence": 0.9,
                "draft": "IMPOSTOR-MARKER draft", "reason": "x", "failsafe": False,
                "answered_by_human": False, "host_outcome": ""}
    ledger.write_text(json.dumps(impostor) + "\n")
    d = compose(make_cfg(tmp_path, shadow_ledger=str(ledger), actuator_dir=None))
    assert d.new == [] and d.waiting == [] and "IMPOSTOR-MARKER" not in d.text
    assert d.left_out_client == 1


def test_config_requires_own_listing_ids(tmp_path):
    for bad in ([], [""], None, "own-maple-0001"):
        with pytest.raises(dg.ConfigError):
            make_cfg(tmp_path, own_property_ids=bad)


# --- window and state ---

def test_first_run_looks_back_24_hours(tmp_path):
    d = compose(make_cfg(tmp_path), watermark=None)
    assert d.window_from == NOW - timedelta(hours=24)
    ids = {i.reservation_id for i in d.new}
    assert "res-s01" in ids and "res-s10" not in ids
    assert len(d.new) == 10


def test_since_the_last_digest_advances_and_held_items_persist(tmp_path):
    cfg = make_cfg(tmp_path)
    first = dg.run(cfg, NOW)
    assert first.outcome == "built" and len(first.digest.new) == 10
    second = dg.run(cfg, NOW + timedelta(hours=1))
    d = second.digest
    assert d.window_from == NOW
    assert (len(d.new), len(d.sends), len(d.disarms), len(d.trips)) == (0, 0, 0, 0)
    assert len(d.waiting) == 5                   # held items are not windowed
    assert "NEW DECISIONS SINCE THE LAST DIGEST (0)\nNone." in d.text


def test_no_advance_is_a_preview(tmp_path):
    cfg = make_cfg(tmp_path)
    a = dg.run(cfg, NOW, advance=False)
    b = dg.run(cfg, NOW, advance=False)
    assert a.digest.window_from == b.digest.window_from
    assert dg.load_state(cfg.out_dir)["watermark_utc"] is None


def _fingerprint(paths):
    out = {}
    for p in paths:
        with open(p, "rb") as fh:
            out[p] = (hashlib.sha256(fh.read()).hexdigest(), os.stat(p).st_mtime_ns)
    return out


def test_sources_are_only_read(tmp_path):
    paths = [SHADOW] + [os.path.join(ACTUATOR, f) for f in dg.ACTUATOR_FILES.values()]
    before = _fingerprint(paths)
    cfg = make_cfg(tmp_path)
    dg.run(cfg, NOW)
    dg.run(cfg, NOW + timedelta(hours=1))
    assert _fingerprint(paths) == before
    assert sorted(os.listdir(ACTUATOR)) == sorted(dg.ACTUATOR_FILES.values())


def test_missing_actuator_logs_are_stated(tmp_path):
    d = compose(make_cfg(tmp_path, actuator_dir=str(tmp_path / "no-actuator")))
    assert "actuator logs not found (it has not run)" in d.text
    assert "SENDS (0)\nNone. The actuator has no logs yet." in d.text
    assert len(d.waiting) == 4 and len(d.trips) == 3


# --- output files ---

def test_digest_and_payload_files_are_private(tmp_path):
    cfg = make_cfg(tmp_path)
    res = dg.run(cfg, NOW)
    assert stat.S_IMODE(os.stat(cfg.out_dir).st_mode) == 0o700
    for p in (res.digest_path, res.payload_path, os.path.join(cfg.out_dir, "digest-log.jsonl"),
              os.path.join(cfg.out_dir, "state.json")):
        assert stat.S_IMODE(os.stat(p).st_mode) == 0o600
    with open(res.digest_path) as fh:
        assert fh.read() == res.digest.text
    with open(res.payload_path) as fh:
        payload = json.load(fh)
    assert payload["schema"] == dg.PAYLOAD_SCHEMA and payload["digest_id"] == res.digest_id
    assert payload["content"] == res.digest.sms
    assert payload["content_sha256"] == hashlib.sha256(res.digest.sms.encode()).hexdigest()
    assert payload["to"] is None and payload["sendable_with_send_flag"] is False
    assert payload["send_blockers"][0].startswith("A2P not confirmed")
    assert payload["counts"] == {"waiting": 5, "new": 10, "sends": 2, "auto_disarms": 1,
                                 "guard_trips": 4, "left_out_client": 2}
    assert log_rows(cfg)[-1]["outcome"] == "built"


def test_out_dir_inside_a_git_work_tree_is_refused(tmp_path):
    inside = os.path.join(os.path.dirname(__file__), "comms-out-should-not-exist")
    with pytest.raises(RepoPathError):
        make_cfg(tmp_path, out_dir=inside)
    assert not os.path.exists(inside)


def test_sms_is_ascii_capped_and_counts_overflow(tmp_path):
    ledger = tmp_path / "many.jsonl"
    rows = [{"decided_at": f"2026-10-30T{13 + n // 6:02d}:{(n % 6) * 10:02d}:00+00:00",
             "property": "Maple Street House", "property_id": MAPLE,
             "reservation_id": f"res-m{n}", "message_at": "", "message_text": "Hi there",
             "engine_category": "Confirm", "confidence": 0.7,
             "draft": "Thanks for letting me know! I’ll check — one moment.",
             "reason": "x", "failsafe": False, "answered_by_human": False, "host_outcome": ""}
            for n in range(30)]
    ledger.write_text("".join(json.dumps(r) + "\n" for r in rows))
    d = compose(make_cfg(tmp_path, shadow_ledger=str(ledger), actuator_dir=None))
    assert len(d.sms) <= dg.SMS_MAX_CHARS
    assert d.sms.isascii()
    assert d.sms.rstrip().endswith("more waiting; the full list is in the digest.")
    assert dg.sms_segments(d.sms) <= 5


def test_times_are_property_local_across_the_dst_change():
    assert dg.fmt_time(datetime(2026, 11, 1, 6, 30, tzinfo=timezone.utc), "America/Chicago") \
        == "Sun Nov 1, 1:30 AM CDT"
    assert dg.fmt_time(datetime(2026, 11, 1, 7, 30, tzinfo=timezone.utc), "America/Chicago") \
        == "Sun Nov 1, 1:30 AM CST"


# --- the operator send gates ---

def test_a2p_false_refuses_the_operator_send(tmp_path):
    cfg = sendable_cfg(tmp_path, a2p_confirmed=False)
    msgs = []
    res = dg.run(cfg, NOW, send=True, transport_factory=no_factory, err=msgs.append)
    assert res.exit_code == dg.EXIT_REFUSED and res.outcome == "refused"
    assert len(res.reasons) == 1 and res.reasons[0].startswith("A2P not confirmed")
    assert any("A2P not confirmed" in m for m in msgs)
    row = log_rows(cfg)[-1]
    assert row["mode"] == "send" and row["outcome"] == "refused"
    assert "A2P 10DLC registration is unverified" in row["reasons"][0]
    state = dg.load_state(cfg.out_dir)
    assert state["watermark_utc"] is None and state["sent"] == {}
    assert os.path.exists(res.digest_path)   # the digest is still built


def test_a2p_must_be_the_json_value_true(tmp_path):
    for value in ("true", "yes", 1, None):
        assert make_cfg(tmp_path, a2p_confirmed=value).a2p_confirmed is False
    assert make_cfg(tmp_path, a2p_confirmed=True).a2p_confirmed is True


def test_the_shipped_default_config_refuses_with_every_reason(tmp_path):
    cfg = make_cfg(tmp_path)
    res = dg.run(cfg, NOW, send=True, transport_factory=no_factory, err=lambda m: None)
    assert res.exit_code == dg.EXIT_REFUSED
    joined = " ".join(res.reasons)
    for part in ("A2P not confirmed", "No operator number", "No sending line", "quo_cli"):
        assert part in joined


@pytest.mark.parametrize("over,needle", [
    ({"operator_number": None}, "No operator number"),
    ({"operator_number": "708-555-0199"}, "operator_number is not a valid E.164"),
    ({"operator_number": LINE}, "operator_number is the sending line itself"),
    ({"from_number": None}, "No sending line"),
])
def test_operator_number_gates(tmp_path, over, needle):
    cfg = sendable_cfg(tmp_path, **over)
    res = dg.run(cfg, NOW, send=True, transport_factory=no_factory, err=lambda m: None)
    assert res.outcome == "refused" and any(needle in r for r in res.reasons)


def test_operator_number_may_not_be_a_crew_number(tmp_path):
    directory = tmp_path / "crew.json"
    directory.write_text(json.dumps({
        "schema": "crew-directory/v1",
        "properties": {MAPLE: {"short_name": "Maple", "roles": {"cleaner": ["w1"]}}},
        "people": {"w1": {"number": OPERATOR, "opted_out": False}}}))
    cfg = sendable_cfg(tmp_path, crew_directory=str(directory))
    res = dg.run(cfg, NOW, send=True, transport_factory=no_factory, err=lambda m: None)
    assert res.reasons == ["operator_number is a crew directory number; the digest goes only "
                           "to the operator's own phone."]
    unreadable = sendable_cfg(tmp_path, crew_directory=str(tmp_path / "missing.json"))
    res = dg.run(unreadable, NOW, send=True, transport_factory=no_factory, err=lambda m: None)
    assert res.outcome == "refused" and "crew directory could not be read" in res.reasons[0]


def test_missing_quo_cli_refuses(tmp_path):
    cfg = sendable_cfg(tmp_path, quo_cli=str(tmp_path / "nope.py"))
    res = dg.run(cfg, NOW, send=True, transport_factory=no_factory, err=lambda m: None)
    assert any("quo_cli" in r for r in res.reasons)


def test_all_gates_hold_sends_once_through_the_mock(tmp_path):
    cfg = sendable_cfg(tmp_path)
    built = []

    def factory(c):
        built.append(MockOperatorTransport(c.operator_number))
        return built[-1]

    res = dg.run(cfg, NOW, send=True, transport_factory=factory)
    assert res.exit_code == dg.EXIT_OK and res.outcome == "sent"
    assert res.message_id == "mock-op-1" and built[0].sent == [res.digest.sms]
    state = dg.load_state(cfg.out_dir)
    assert state["watermark_utc"] == NOW.isoformat()
    assert state["sent"][res.digest_id]["message_id"] == "mock-op-1"
    assert [r["outcome"] for r in log_rows(cfg)] == ["sending", "sent"]


def test_a_digest_is_never_sent_twice(tmp_path):
    cfg = sendable_cfg(tmp_path)
    factory = lambda c: MockOperatorTransport(c.operator_number)  # noqa: E731
    first = dg.run(cfg, NOW, send=True, advance=False, transport_factory=factory)
    second = dg.run(cfg, NOW, send=True, advance=False, transport_factory=no_factory,
                    err=lambda m: None)
    assert first.outcome == "sent" and second.digest_id == first.digest_id
    assert second.outcome == "refused" and "never sent twice" in second.reasons[0]


def test_an_unclear_send_is_never_retried(tmp_path):
    cfg = sendable_cfg(tmp_path)
    boom = lambda c: MockOperatorTransport(c.operator_number, raises=TimeoutError("slow"))  # noqa: E731
    res = dg.run(cfg, NOW, send=True, transport_factory=boom, err=lambda m: None)
    assert res.exit_code == dg.EXIT_SEND_FAILED and res.outcome == "unknown"
    state = dg.load_state(cfg.out_dir)
    assert state["watermark_utc"] is None
    assert state["sent"][res.digest_id]["outcome"] == "unknown"
    again = dg.run(cfg, NOW, send=True, transport_factory=no_factory, err=lambda m: None)
    assert again.outcome == "refused"


def test_a_transport_bound_to_another_number_is_refused(tmp_path):
    cfg = sendable_cfg(tmp_path)
    other = lambda c: MockOperatorTransport("+15555550111")  # noqa: E731
    res = dg.run(cfg, NOW, send=True, transport_factory=other, err=lambda m: None)
    assert res.outcome == "refused" and "not bound to the configured operator" in res.reasons[0]
    assert dg.load_state(cfg.out_dir)["sent"] == {}


def test_cli_builds_only_by_default(tmp_path, capsys):
    path = tmp_path / "cfg.json"
    path.write_text(json.dumps(raw_cfg(tmp_path)))
    assert dg.main(["--config", str(path), "--now", NOW.isoformat()]) == dg.EXIT_OK
    out = capsys.readouterr().out
    assert ": built" in out and "not sent (build only)" in out


def test_cli_send_with_the_shipped_gates_is_refused_before_any_transport(tmp_path, capsys):
    # The real factory is in play; subprocess and sockets raise if touched.
    path = tmp_path / "cfg.json"
    cli = tmp_path / "quo.py"
    cli.write_text("# never executed\n")
    path.write_text(json.dumps(raw_cfg(tmp_path, operator_number=OPERATOR, from_number=LINE,
                                       quo_cli=str(cli))))
    assert dg.main(["--config", str(path), "--now", NOW.isoformat(), "--send"]) == dg.EXIT_REFUSED
    assert "REFUSED: A2P not confirmed" in capsys.readouterr().err


def test_cli_config_error_exits_2(tmp_path, capsys):
    path = tmp_path / "cfg.json"
    path.write_text(json.dumps(raw_cfg(tmp_path, own_property_ids=[])))
    assert dg.main(["--config", str(path)]) == dg.EXIT_CONFIG


# --- the real Quo transport, without running it ---

def test_quo_cli_transport_builds_one_send_to_the_operator():
    t = QuoCliOperatorTransport("/opt/x/quo.py", LINE, OPERATOR)
    assert t.argv("hi") == [sys.executable, "/opt/x/quo.py", "send", "--from", LINE,
                            "--to", OPERATOR, "--content", "hi"]
    with pytest.raises(RecipientError):
        QuoCliOperatorTransport("/opt/x/quo.py", LINE, LINE)
    with pytest.raises(RecipientError):
        QuoCliOperatorTransport("/opt/x/quo.py", LINE, [OPERATOR, "+15555550111"])


class _Proc:
    def __init__(self, code, out="", err=""):
        self.returncode, self.stdout, self.stderr = code, out, err


def test_quo_cli_transport_reads_results_from_a_fake_runner():
    calls = []

    def runner(result):
        def run(argv, **kw):
            calls.append(kw["env"].get("PYTHONPATH", ""))
            if isinstance(result, Exception):
                raise result
            return result
        return run

    def t(result):
        return QuoCliOperatorTransport("/opt/x/quo.py", LINE, OPERATOR,
                                       pythonpath="/opt/helper", runner=runner(result))

    assert t(_Proc(0, json.dumps({"data": {"id": "AC123"}}))).send("hi").message_id == "AC123"
    assert t(_Proc(1, err="HTTP 400 POST /v1/messages: bad")).send("hi").status == FAILED
    assert t(_Proc(1, err="HTTP 502 POST /v1/messages")).send("hi").status == "unknown"
    assert t(_Proc(0, "not json")).send("hi").status == "unknown"
    assert t(_Proc(0, json.dumps({"data": {}}))).send("hi").status == "unknown"
    assert t(subprocess.TimeoutExpired("quo", 45)).send("hi").status == "unknown"
    assert all(c.startswith("/opt/helper") for c in calls)
