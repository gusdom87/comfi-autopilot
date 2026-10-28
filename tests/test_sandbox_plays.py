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
                           "property_id": "lockport-2n",
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
    assert "Cristina" in ping["result"]["draft"]
    assert _step(out, "wait_cleaner_done")["status"] == "waiting"
    assert "early_checkin_charge" in out["awaiting_approval"]
    # The run is in the store and fetchable by id.
    assert srv.handle_get_run(out["run_id"], runs=runs)["run_id"] == out["run_id"]


def test_event_cleaner_done_completes_the_chain():
    srv, engine, runs, out = _start_ec(_srv())
    done = srv.handle_event({"run_id": out["run_id"], "event": "cleaner_done",
                             "payload": {"cleaner_id": "cleaner-cristina",
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
                           "property_id": "lockport-2n", "date": "2026-10-28",
                           "cleaner_id": "cleaner-cristina",
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
                           "property_id": "peters-house", "date": "2026-10-28",
                           "cleaner_id": "cleaner-jader",
                           "finished_at": "3:05 PM"},
                          engine=engine, runs=runs)
    assert out["status"] == "held_for_human"
    assert "Cracked wine glass" in out["hold_reason"]
    send = _step(out, "send_guest_ready")
    assert send["status"] == "pending" and send["result"] == {}


def test_play_camera_check_gathering_notifies_operator_held():
    srv = _srv()
    engine, runs = _fresh()
    out = srv.handle_play({"play": "camera_check",
                           "property_id": "lockport-1n", "date": "2026-10-29",
                           "camera": "backyard", "ts": "2026-10-29T21:48:00",
                           "frame_id": "f3"},
                          engine=engine, runs=runs)
    assert out["status"] == "completed" and out["outcome"] == "notify_operator"
    classify = _step(out, "classify_frame")
    assert classify["result"]["scene"] == "large_gathering"
    assert classify["result"]["people_count"] == 11
    notify = _step(out, "notify_operator")
    assert notify["action_key"] == "notify_operator"
    assert notify["stake"] == "middle"
    assert notify["disposition"] == "hold" and notify["status"] == "held"
    assert notify["result"]["executed"] is False
    assert "party of 4" in notify["result"]["summary"]
    assert _step(out, "log_event")["status"] == "skipped"
    # The run is in the store and fetchable by id.
    assert srv.handle_get_run(out["run_id"], runs=runs)["run_id"] == out["run_id"]


def test_play_camera_check_expected_guest_logs_only():
    srv = _srv()
    engine, runs = _fresh()
    out = srv.handle_play({"play": "camera_check",
                           "property_id": "lockport-2n", "date": "2026-10-28",
                           "camera": "front-door", "ts": "2026-10-28T15:42:00",
                           "frame_id": "f1"},
                          engine=engine, runs=runs)
    assert out["status"] == "completed" and out["outcome"] == "logged"
    assert _step(out, "classify_frame")["result"]["scene"] == "expected_guest"
    assert _step(out, "log_event")["result"]["logged"] is True
    assert _step(out, "notify_operator")["status"] == "skipped"


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
                                             "property_id": "lockport-2n",
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
