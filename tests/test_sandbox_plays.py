import importlib.util, json, os, sys, threading, urllib.request
from http.server import ThreadingHTTPServer

ROOT = os.path.join(os.path.dirname(__file__), "..")
sys.path.insert(0, os.path.join(ROOT, "src"))
os.environ["JEV_SPEND_STATE"] = os.path.join(os.path.dirname(__file__), "spend-plays-test.json")

from jev.plays import FixtureContext, PlayEngine, default_fixture_data


def load_mod(name, rel):
    spec = importlib.util.spec_from_file_location(name, os.path.join(ROOT, rel))
    mod = importlib.util.module_from_spec(spec); spec.loader.exec_module(mod)
    return mod


def _srv():
    return load_mod("sandbox_server_plays", "sandbox/server.py")


def _fresh():
    return PlayEngine(FixtureContext(default_fixture_data())), {}


def _step(run, name):
    return next(s for s in run["steps"] if s["name"] == name)


EC_MSG = ("Hi! Our flight lands early - could we check in at 1pm "
          "instead of 4? Happy to pay an early check-in fee.")


def _start_ec(srv):
    engine, runs = _fresh()
    out = srv.handle_play({"play": "early_checkin", "message": EC_MSG,
                           "property_id": "cedar-2a",
                           "date": "2026-10-28"}, engine=engine, runs=runs)
    return srv, engine, runs, out


def test_play_early_checkin_conditional_charge_held_and_waits():
    srv, engine, runs, out = _start_ec(_srv())
    assert out["play"] == "early_checkin" and out["status"] == "waiting"
    assert _step(out, "reality_check")["result"]["outcome"] == "conditional"

    charge = _step(out, "early_checkin_charge")
    assert charge["kind"] == "action" and charge["action_key"] == "charge_guest"
    assert charge["stake"] == "highest"
    assert charge["required_right"] == "approve_first"
    assert charge["disposition"] == "hold" and charge["status"] == "held"
    assert charge["result"]["charge"]["amount_usd"] == 25.0
    assert charge["result"]["executed"] is False

    ping = _step(out, "cleaner_ping")
    assert ping["action_key"] == "message_cleaner" and ping["status"] == "held"
    assert "Lena" in ping["result"]["draft"]
    assert _step(out, "wait_cleaner_done")["status"] == "waiting"
    assert "early_checkin_charge" in out["awaiting_approval"]
    # The run is in the store and fetchable by id.
    assert srv.handle_get_run(out["run_id"], runs=runs)["run_id"] == out["run_id"]


def test_event_cleaner_done_completes_the_chain():
    srv, engine, runs, out = _start_ec(_srv())
    done = srv.handle_event({"run_id": out["run_id"], "event": "cleaner_done",
                             "payload": {"cleaner_id": "cleaner-lena",
                                         "finished_at": "1:32 PM"}},
                            engine=engine, runs=runs)
    assert done["status"] == "completed"
    assert _step(done, "wait_cleaner_done")["status"] == "done"
    update = _step(done, "guest_ready_update")
    assert "ready for you now" in update["result"]["draft"]
    assert update["status"] == "held"          # message_guest still P1
    assert update["result"]["executed"] is False


def test_play_cleaner_done_happy_path_send_held():
    srv = _srv()
    engine, runs = _fresh()
    out = srv.handle_play({"play": "cleaner_done",
                           "property_id": "cedar-2a", "date": "2026-10-28",
                           "cleaner_id": "cleaner-lena",
                           "finished_at": "1:32 PM"},
                          engine=engine, runs=runs)
    assert out["status"] == "completed" and out["outcome"] == "ready"
    send = _step(out, "send_guest_ready")
    assert "Dana" in send["result"]["draft"]
    assert send["disposition"] == "hold" and send["status"] == "held"
    assert send["result"]["executed"] is False


def test_play_cleaner_done_problem_flag_holds_and_sends_nothing():
    srv = _srv()
    engine, runs = _fresh()
    out = srv.handle_play({"play": "cleaner_done",
                           "property_id": "maple-house", "date": "2026-10-28",
                           "cleaner_id": "cleaner-tomas",
                           "finished_at": "3:05 PM"},
                          engine=engine, runs=runs)
    assert out["status"] == "held_for_human"
    assert "Cracked wine glass" in out["hold_reason"]
    send = _step(out, "send_guest_ready")
    assert send["status"] == "pending" and send["result"] == {}


def _gathering_frame(srv, engine, runs, frame_id, ts):
    return srv.handle_play({"play": "camera_check",
                            "property_id": "cedar-1a", "date": "2026-10-29",
                            "camera": "backyard", "ts": ts,
                            "frame_id": frame_id},
                           engine=engine, runs=runs)


def test_play_camera_check_gathering_notifies_operator_held():
    # Batch 3 (case #6): f3 alone logs; the sustained run f26, f27,
    # f3 is what notifies.
    from jev.plays.camera_check import _reset_gathering_frames
    _reset_gathering_frames()
    srv = _srv()
    engine, runs = _fresh()
    alone = _gathering_frame(srv, engine, runs, "f3", "2026-10-29T21:48:00")
    assert alone["outcome"] == "logged"
    _reset_gathering_frames()
    engine, runs = _fresh()
    for frame_id, ts in (("f26", "2026-10-29T21:20:00"),
                         ("f27", "2026-10-29T21:35:00")):
        assert _gathering_frame(srv, engine, runs, frame_id, ts)["outcome"] \
            == "logged"
    out = _gathering_frame(srv, engine, runs, "f3", "2026-10-29T21:48:00")
    assert out["status"] == "completed" and out["outcome"] == "notify_operator"
    classify = _step(out, "classify_frame")
    assert classify["result"]["scene"] == "large_gathering"
    assert classify["result"]["people_count"] == 11
    assert classify["result"]["gathering"]["floor"] == 9
    notify = _step(out, "notify_operator")
    assert notify["action_key"] == "notify_operator"
    assert notify["stake"] == "middle"
    assert notify["disposition"] == "hold" and notify["status"] == "held"
    assert notify["result"]["executed"] is False
    assert "party of 4" in notify["result"]["summary"]
    assert "at least 9 people (model estimate" in notify["result"]["summary"]
    assert _step(out, "log_event")["status"] == "skipped"
    # The run is in the store and fetchable by id.
    assert srv.handle_get_run(out["run_id"], runs=runs)["run_id"] == out["run_id"]


def test_play_camera_check_expected_guest_logs_only():
    srv = _srv()
    engine, runs = _fresh()
    out = srv.handle_play({"play": "camera_check",
                           "property_id": "cedar-2a", "date": "2026-10-28",
                           "camera": "front-door", "ts": "2026-10-28T15:42:00",
                           "frame_id": "f1"},
                          engine=engine, runs=runs)
    assert out["status"] == "completed" and out["outcome"] == "logged"
    assert _step(out, "classify_frame")["result"]["scene"] == "expected_guest"
    assert _step(out, "log_event")["result"]["logged"] is True
    assert _step(out, "notify_operator")["status"] == "skipped"


def test_play_camera_offline_switch_message_held_with_wording():
    srv = _srv()
    engine, runs = _fresh()
    out = srv.handle_play({"play": "camera_offline",
                           "property_id": "cedar-1a",
                           "date": "2026-10-29", "camera": "front-door",
                           "ts": "2026-10-29T19:20:00",
                           "mention": "lights_and_camera"},
                          engine=engine, runs=runs)
    assert out["play"] == "camera_offline"
    assert out["status"] == "completed"
    assert out["outcome"] == "switch_message_held"
    reality = _step(out, "check_reality")
    assert reality["result"]["switch_fed"] is True
    assert reality["result"]["mention"] == "lights_and_camera"
    msg = _step(out, "guest_switch_message")
    assert msg["action_key"] == "message_guest"
    assert msg["disposition"] == "hold" and msg["status"] == "held"
    assert msg["result"]["executed"] is False
    assert "outside camera" in msg["result"]["draft"]
    assert srv.handle_get_run(out["run_id"], runs=runs)["run_id"] \
        == out["run_id"]

    # The camera comes back before the held message ever executes:
    # the run closes as resolved through the event endpoint.
    back = srv.handle_event({"run_id": out["run_id"],
                             "event": "camera_online",
                             "payload": {"camera": "front-door"}},
                            engine=engine, runs=runs)
    assert back["outcome"] == "resolved_camera_back"


def test_play_camera_check_vacant_mode_notifies_with_still():
    srv = _srv()
    engine, runs = _fresh()
    out = srv.handle_play({"play": "camera_check",
                           "property_id": "maple-house",
                           "date": "2026-10-30", "camera": "front-door",
                           "ts": "2026-10-30T22:15:00", "frame_id": "f5"},
                          engine=engine, runs=runs)
    assert out["status"] == "completed" and out["outcome"] == "notify_operator"
    assert _step(out, "match_reality")["result"]["vacant"] is True
    notify = _step(out, "notify_operator")
    assert notify["status"] == "held"
    assert notify["result"]["executed"] is False
    assert "frame f5" in notify["result"]["summary"]
    assert "attached in the simulated" in notify["result"]["summary"]


def test_play_camera_covered_view_blocked_urgent():
    srv = _srv()
    engine, runs = _fresh()
    out = srv.handle_play({"play": "camera_check",
                           "property_id": "cedar-1a",
                           "date": "2026-10-30", "camera": "front-door",
                           "ts": "2026-10-30T21:05:00", "frame_id": "f10"},
                          engine=engine, runs=runs)
    assert out["status"] == "completed" and out["outcome"] == "notify_operator"
    classify = _step(out, "classify_frame")
    assert classify["result"]["scene"] == "nothing_notable"
    assert classify["result"]["view"] == "blocked"
    assert classify["result"]["route"] == "notify"
    notify = _step(out, "notify_operator")
    assert notify["disposition"] == "hold" and notify["status"] == "held"
    assert notify["result"]["executed"] is False
    assert notify["result"]["urgent"] is True
    assert "One-night weekend stay" in notify["result"]["summary"]


def test_play_guest_lockout_notifies_operator_held():
    srv = _srv()
    engine, runs = _fresh()
    out = srv.handle_play({"play": "guest_lockout",
                           "property_id": "cedar-2a",
                           "date": "2026-10-28", "camera": "front-door",
                           "ts": "2026-10-28T15:52:00", "frame_id": "f13"},
                          engine=engine, runs=runs)
    assert out["play"] == "guest_lockout"
    assert out["status"] == "completed"
    assert out["outcome"] == "notify_operator"
    assemble = _step(out, "assemble_door_frame")
    assert assemble["result"]["frame_id"] == "f13"
    assert assemble["result"]["door"] == "closed"
    notify = _step(out, "notify_operator")
    assert notify["action_key"] == "notify_operator"
    assert notify["disposition"] == "hold" and notify["status"] == "held"
    assert notify["result"]["executed"] is False
    assert "a person is at the door and the door reads shut" \
        in notify["result"]["summary"]
    assert srv.handle_get_run(out["run_id"], runs=runs)["run_id"] \
        == out["run_id"]


def test_play_guest_unsafe_assembles_latest_frames_urgent():
    srv = _srv()
    engine, runs = _fresh()
    out = srv.handle_play({"play": "guest_unsafe",
                           "property_id": "cedar-2a",
                           "date": "2026-10-28", "kind": "guest_unsafe"},
                          engine=engine, runs=runs)
    assert out["play"] == "guest_unsafe"
    assert out["status"] == "completed"
    assert out["outcome"] == "notify_operator"
    assemble = _step(out, "assemble_latest_frames")
    assert assemble["result"]["frames"] == ["f16", "f14", "f13"]
    notify = _step(out, "notify_operator")
    assert notify["disposition"] == "hold" and notify["status"] == "held"
    assert notify["result"]["executed"] is False
    assert notify["result"]["urgent"] is True
    assert notify["result"]["summary"].startswith("URGENT")


def test_play_sunset_check_second_night_bulb_line():
    srv = _srv()
    engine, runs = _fresh()
    out = srv.handle_play({"play": "camera_sunset_check",
                           "property_id": "cedar-2a",
                           "date": "2026-10-28", "kind": "sunset_check",
                           "camera": "front-door",
                           "ts": "2026-10-28T18:47:00", "frame_id": "f16"},
                          engine=engine, runs=runs)
    assert out["play"] == "camera_sunset_check"
    assert out["status"] == "completed"
    assert out["outcome"] == "notify_operator"
    entry = _step(out, "classify_entry")
    assert entry["result"]["lights"] == "off"
    assert entry["result"]["second_night"] is True
    notify = _step(out, "notify_operator")
    assert notify["status"] == "held"
    assert notify["result"]["executed"] is False
    assert "bulb change belongs on the next turnover list" \
        in notify["result"]["summary"]


def test_play_camera_post_turnover_door_open_notifies():
    srv = _srv()
    engine, runs = _fresh()
    out = srv.handle_play({"play": "camera_post_turnover",
                           "property_id": "cedar-2a",
                           "date": "2026-10-28", "kind": "post_turnover",
                           "camera": "front-door",
                           "ts": "2026-10-28T14:15:00", "frame_id": "f7"},
                          engine=engine, runs=runs)
    assert out["play"] == "camera_post_turnover"
    assert out["outcome"] == "notify_operator"
    state = _step(out, "classify_state")
    assert state["result"]["door"] == "open"
    assert state["result"]["lights"] == "off"
    assert _step(out, "notify_operator")["status"] == "held"


IH_LEAK = ("Hi, there's water leaking under the kitchen sink - it's "
           "dripping steadily and the cabinet floor is wet.")
IH_GAS = "Hi, I smell gas near the stove, kind of strong. Should I be worried?"


def test_play_in_stay_issue_dispatch_held_and_event_flow():
    srv = _srv()
    engine, runs = _fresh()
    out = srv.handle_play({"play": "in_stay_issue", "message": IH_LEAK,
                           "property_id": "cedar-1a",
                           "date": "2026-10-29"}, engine=engine, runs=runs)
    assert out["play"] == "in_stay_issue" and out["status"] == "waiting"
    assert out["outcome"] == "leak"
    reality = _step(out, "reality_check")
    assert reality["result"]["stay_in_progress"] is True
    assert reality["result"]["guest"] == "Alex"
    assert reality["result"]["vendor"] == "Fixture Handyman"

    dispatch = _step(out, "dispatch_vendor")
    assert dispatch["action_key"] == "dispatch_vendor"
    assert dispatch["stake"] == "middle"
    assert dispatch["required_right"] == "approve_first"
    assert dispatch["disposition"] == "hold" and dispatch["status"] == "held"
    assert dispatch["result"]["executed"] is False
    assert "Water shutoff" in dispatch["result"]["access_notes"]
    # A standard issue never flags the operator.
    assert _step(out, "notify_operator")["status"] == "skipped"
    assert _step(out, "wait_vendor_eta")["status"] == "waiting"

    eta = srv.handle_event({"run_id": out["run_id"], "event": "vendor_eta",
                            "payload": {"eta": "3:30 PM"}},
                           engine=engine, runs=runs)
    assert eta["status"] == "waiting"
    update = _step(eta, "guest_eta_update")
    assert "3:30 PM" in update["result"]["draft"]
    assert update["status"] == "held"
    assert update["result"]["executed"] is False

    done = srv.handle_event({"run_id": out["run_id"], "event": "vendor_done",
                             "payload": {"finished_at": "4:05 PM"}},
                            engine=engine, runs=runs)
    assert done["status"] == "completed"
    final = _step(done, "guest_done_update")
    assert "fixed" in final["result"]["draft"]
    assert srv.handle_get_run(out["run_id"], runs=runs)["run_id"] == out["run_id"]


def test_play_in_stay_issue_gas_safety_notifies_operator():
    srv = _srv()
    engine, runs = _fresh()
    out = srv.handle_play({"play": "in_stay_issue", "message": IH_GAS,
                           "property_id": "cedar-1a",
                           "date": "2026-10-29"}, engine=engine, runs=runs)
    assert out["outcome"] == "safety_gas_smell"
    reply = _step(out, "guest_reply")
    assert "head outside" in reply["result"]["draft"]
    assert "towel" not in reply["result"]["draft"]   # no self-help
    notify = _step(out, "notify_operator")
    assert notify["action_key"] == "notify_operator"
    assert notify["disposition"] == "hold" and notify["status"] == "held"
    assert notify["result"]["executed"] is False
    assert notify["result"]["summary"].startswith("SAFETY")
    assert "Garden Unit 1A" in notify["result"]["summary"]
    assert _step(out, "dispatch_vendor")["status"] == "held"


def test_play_and_event_error_paths():
    srv = _srv()
    engine, runs = _fresh()
    assert "error" in srv.handle_play({"play": "nope"}, engine=engine, runs=runs)
    assert "error" in srv.handle_play({"play": "cleaner_done"},
                                      engine=engine, runs=runs)
    assert "error" in srv.handle_event({"run_id": "nope", "event": "cleaner_done"},
                                       engine=engine, runs=runs)
    assert "error" in srv.handle_get_run("nope", runs=runs)


def test_http_endpoints_round_trip():
    srv = _srv()
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), srv.Handler)
    port = httpd.server_address[1]
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{port}"

    def req(method, path, payload=None):
        data = json.dumps(payload).encode() if payload is not None else None
        r = urllib.request.Request(base + path, data=data, method=method,
                                   headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(r, timeout=5) as resp:
            return resp.status, resp.read().decode()

    try:
        status, body = req("GET", "/")
        assert status == 200 and "Plays: the operation at work" in body
        assert body.count("data-msg") == 4     # triage chips untouched

        status, body = req("POST", "/play", {"play": "early_checkin",
                                             "message": EC_MSG,
                                             "property_id": "cedar-2a",
                                             "date": "2026-10-28"})
        run = json.loads(body)
        assert status == 200 and run["status"] == "waiting"

        status, body = req("POST", "/event", {"run_id": run["run_id"],
                                              "event": "cleaner_done",
                                              "payload": {"finished_at": "1:32 PM"}})
        assert status == 200 and json.loads(body)["status"] == "completed"

        status, body = req("GET", f"/run/{run['run_id']}")
        assert status == 200 and json.loads(body)["run_id"] == run["run_id"]
    finally:
        httpd.shutdown()


# ---------------------------------------------------------------------
# Play 7 batch 2: dog, snow, outage cluster, upkeep (sandbox layer)
# ---------------------------------------------------------------------

def test_sandbox_dog_arrival_flags_operator():
    srv = _srv()
    engine, runs = _fresh()
    out = srv.handle_play({
        "play": "camera_check", "property_id": "maple-house",
        "date": "2026-10-28", "camera": "front-door",
        "ts": "2026-10-28T15:58:00", "frame_id": "f19"},
        engine=engine, runs=runs)
    assert not out.get("error")
    assert out["outcome"] == "notify_operator"
    notify = _step(out, "notify_operator")
    assert "a dog-sized animal was seen arriving with the party" \
        in notify["result"]["summary"]


def test_sandbox_morning_check_snow_at_empty_home():
    srv = _srv()
    engine, runs = _fresh()
    out = srv.handle_play({
        "play": "camera_morning_check", "property_id": "maple-house",
        "date": "2026-10-29", "kind": "morning_check",
        "camera": "front-door", "ts": "2026-10-29T10:30:00",
        "frame_id": "f21"}, engine=engine, runs=runs)
    assert not out.get("error")
    assert out["play"] == "camera_morning_check"
    assert out["outcome"] == "notify_operator"
    assert _step(out, "classify_ground")["result"]["ground"] \
        == "snow_covered"


def test_sandbox_outage_cluster_burst():
    from jev.plays.camera_check import _reset_outage_correlator
    _reset_outage_correlator()
    srv = _srv()
    engine, runs = _fresh()
    out = srv.handle_play({
        "play": "camera_outage_cluster", "events": [
            {"property_id": "cedar-1a", "date": "2026-10-29",
             "camera": "front-door", "ts": "2026-10-29T19:20:00",
             "mention": "lights_and_camera"},
            {"property_id": "cedar-2a", "date": "2026-10-29",
             "camera": "front-door", "ts": "2026-10-29T19:26:00"},
            {"property_id": "cedar-1a", "date": "2026-10-29",
             "camera": "backyard", "ts": "2026-10-29T19:31:00"}]},
        engine=engine, runs=runs)
    assert not out.get("error")
    assert out["cluster"] is not None
    assert out["cluster"]["cameras"] == 3
    cluster_run = out["cluster_run"]
    assert cluster_run is not None
    assert cluster_run["outcome"] == "area_outage_notified"
    for run_dict in out["runs"]:
        names = [s["name"] for s in run_dict["steps"]]
        assert "outage_cluster" in names


def test_sandbox_outage_second_click_joins_announced_cluster():
    # The demo button clicked twice on the shared engine: the second
    # burst raises no new cluster run, and its runs carry the
    # outage_cluster mark the page reads to say so (instead of
    # "below the correlation threshold").
    from jev.plays.camera_check import _reset_outage_correlator
    _reset_outage_correlator()
    srv = _srv()
    engine, runs = _fresh()
    payload = {
        "play": "camera_outage_cluster", "events": [
            {"property_id": "cedar-1a", "date": "2026-10-29",
             "camera": "front-door", "ts": "2026-10-29T19:20:00",
             "mention": "lights_and_camera"},
            {"property_id": "cedar-2a", "date": "2026-10-29",
             "camera": "front-door", "ts": "2026-10-29T19:26:00"},
            {"property_id": "cedar-1a", "date": "2026-10-29",
             "camera": "backyard", "ts": "2026-10-29T19:31:00"}]}
    first = srv.handle_play(payload, engine=engine, runs=runs)
    assert len(first["cluster_runs"]) == 1
    assert first["cluster_runs"][0]["run_id"] in runs
    cid = first["cluster"]["cluster_id"]
    second = srv.handle_play(payload, engine=engine, runs=runs)
    assert not second.get("error")
    assert second["cluster_run"] is None and second["cluster_runs"] == []
    for run_dict in second["runs"]:
        assert _step(run_dict, "outage_cluster")["result"]["cluster_id"] == cid
    html = open(os.path.join(ROOT, "sandbox", "index.html")).read()
    assert "joined the already-announced cluster" in html
    assert "no second notification was raised" in html


def test_sandbox_upkeep_report():
    srv = _srv()
    engine, runs = _fresh()
    out = srv.handle_play({
        "play": "camera_upkeep", "date": "2026-10-28",
        "kind": "camera_upkeep"}, engine=engine, runs=runs)
    assert not out.get("error")
    assert out["play"] == "camera_upkeep"
    assert out["outcome"] == "upkeep_report"
    build = _step(out, "build_report")
    assert len(build["result"]["notes"]) == 3


# ---------------------------------------------------------------------
# Play 7 batch 3: gathering accumulation, vendor proof, move-in signs
# ---------------------------------------------------------------------

def _vendor_play(srv, engine, runs, **over):
    body = {"play": "camera_vendor_proof", "property_id": "maple-house",
            "date": "2026-10-30", "vendor": "Fixture Handyman",
            "window_start": "10:00", "window_end": "12:00"}
    body.update(over)
    opened = srv.handle_play(body, engine=engine, runs=runs)
    assert not opened.get("error")
    assert opened["status"] == "waiting"
    assert _step(opened, "wait_window_closed")["status"] == "waiting"
    return srv.handle_event({"run_id": opened["run_id"],
                             "event": "vendor_window_closed",
                             "payload": {}}, engine=engine, runs=runs)


def test_sandbox_vendor_proof_sighting_logs_proof_with_frame():
    srv = _srv()
    engine, runs = _fresh()
    out = _vendor_play(srv, engine, runs)
    assert out["play"] == "camera_vendor_proof"
    assert out["status"] == "completed"
    assert out["outcome"] == "proof_of_service_logged"
    proof = _step(out, "log_proof")
    assert proof["action_key"] == "auto_close"
    assert proof["result"]["frame_id"] == "f6"
    assert "Presence evidence only" in proof["result"]["entry"]
    assert _step(out, "notify_operator")["status"] == "skipped"
    assert srv.handle_get_run(out["run_id"], runs=runs)["run_id"] \
        == out["run_id"]


def test_sandbox_vendor_proof_no_sighting_notifies_operator_held():
    srv = _srv()
    engine, runs = _fresh()
    out = _vendor_play(srv, engine, runs, property_id="cedar-1a",
                       date="2026-10-29", window_start="15:00",
                       window_end="16:00")
    assert out["outcome"] == "notify_operator"
    notify = _step(out, "notify_operator")
    assert notify["disposition"] == "hold" and notify["status"] == "held"
    assert notify["result"]["executed"] is False
    assert "no vendor was seen inside the stated window" \
        in notify["result"]["summary"]


def test_sandbox_vendor_proof_bad_window_is_an_error():
    srv = _srv()
    engine, runs = _fresh()
    out = srv.handle_play({"play": "camera_vendor_proof",
                           "property_id": "maple-house",
                           "date": "2026-10-30", "window_start": "noon",
                           "window_end": "13:00"},
                          engine=engine, runs=runs)
    assert "error" in out


def test_sandbox_move_in_signs_sequence():
    from jev.plays.camera_check import _reset_move_in_signs
    _reset_move_in_signs()
    srv = _srv()
    engine, runs = _fresh()
    outcomes = []
    for frame_id in ("f29", "f30", "f31", "f32"):
        rec = {"f29": ("2026-11-02", "14:10"), "f30": ("2026-11-03", "18:05"),
               "f31": ("2026-11-04", "17:40"),
               "f32": ("2026-11-05", "19:15")}[frame_id]
        out = srv.handle_play({"play": "camera_check",
                               "property_id": "cedar-2a", "date": rec[0],
                               "camera": "front-door",
                               "ts": f"{rec[0]}T{rec[1]}:00",
                               "frame_id": frame_id},
                              engine=engine, runs=runs)
        outcomes.append(out["outcome"])
    assert outcomes == ["logged", "logged", "logged", "notify_operator"]
    notify = _step(out, "notify_operator")
    assert notify["status"] == "held"
    assert notify["result"]["awareness_only"] is True
    assert "signs consistent with a possible move-in" \
        in notify["result"]["summary"]


def test_sandbox_page_wires_the_batch3_buttons():
    html = open(os.path.join(ROOT, "sandbox", "index.html")).read()
    for button in ("cc-gathering-sustained", "cc-vendor-seen",
                   "cc-vendor-missing", "cc-movein"):
        assert f'id="{button}"' in html, button
    assert "'camera_vendor_proof'" in html
    assert "'vendor_window_closed'" in html
