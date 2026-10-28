"""Judge sandbox server (stdlib only). Serves the page, POST /triage,
and the plays endpoints (POST /play, POST /event, GET /run/<id>).
All model calls happen server-side; the API key never reaches the
browser. Play runs live in memory, on the staged fixture world; every
play effect is simulated or held for approval - nothing real is sent.
Run: JEV_SANDBOX_PORT=8791 python3 sandbox/server.py"""
import json
import os
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
from jev.autonomy import load_ledger, right_for  # noqa: E402
from jev.plays import FixtureContext, PlayEngine, default_fixture_data  # noqa: E402
from jev.plays import get as get_play  # noqa: E402
from jev.plays.camera_check import (  # noqa: E402
    handle_camera_event, handle_camera_offline_event,
    handle_camera_online_event, handle_post_turnover_event)
from jev.plays.cleaner_done import handle_cleaner_done_event  # noqa: E402
from jev.tiers import decide as _decide  # noqa: E402
from jev.types import FAIL_SAFE  # noqa: E402

HERE = os.path.dirname(__file__)


def handle_triage(payload: dict, decide_fn=_decide, ledger_path: str = "") -> dict:
    message = str(payload.get("message", "")).strip()
    if not message:
        return {"error": "empty message"}
    context = {"property": str(payload.get("property", ""))}
    d = decide_fn("escalate", message, context)
    ledger = load_ledger(ledger_path or os.environ.get("JEV_LEDGER", ""))
    return {
        "category": d.category.value,
        "confidence": d.confidence,
        "draft": d.draft,
        "route": d.route,
        "reason": d.reason,
        "tier_path": d.tier_path,
        "cost_usd": round(d.cost_usd, 6),
        "autonomy_right": right_for(d.category.value, ledger),
        "failsafe": d.reason == FAIL_SAFE.reason,
    }


# -- plays -------------------------------------------------------------
# One engine over the staged fixture world, shared by every request.
# The autonomy ledger is empty here, so every action right is P1
# (approve_first): drafts are produced and steps are HELD, exactly
# what the demo needs to show. Runs are kept in memory by run_id.
ENGINE = PlayEngine(FixtureContext(default_fixture_data()))
RUNS: dict = {}


def handle_play(payload: dict, engine=ENGINE, runs=None) -> dict:
    """Start a play run. early_checkin starts from a scenario message +
    property + date; cleaner_done starts by delivering its event
    (the cleaner's finish is the trigger). Returns the run's dict."""
    runs = RUNS if runs is None else runs
    play_name = str(payload.get("play", "")).strip()
    if play_name == "camera_check":
        event = {k: str(payload.get(k, "")).strip()
                 for k in ("property_id", "date", "camera", "ts",
                           "frame_id")}
        try:
            run = handle_camera_event(engine, event)
        except ValueError as exc:
            return {"error": str(exc)}
        runs[run.run_id] = run
        return run.to_dict()
    if play_name == "camera_offline":
        event = {k: str(payload.get(k, "")).strip()
                 for k in ("property_id", "date", "camera", "ts",
                           "mention")}
        try:
            run = handle_camera_offline_event(engine, event)
        except ValueError as exc:
            return {"error": str(exc)}
        runs[run.run_id] = run
        return run.to_dict()
    if play_name == "camera_post_turnover":
        event = {k: str(payload.get(k, "")).strip()
                 for k in ("property_id", "date", "kind", "camera",
                           "ts", "frame_id")}
        try:
            run = handle_post_turnover_event(engine, event)
        except ValueError as exc:
            return {"error": str(exc)}
        runs[run.run_id] = run
        return run.to_dict()
    if play_name == "cleaner_done":
        event = {k: str(payload.get(k, "")).strip()
                 for k in ("property_id", "date", "cleaner_id",
                           "finished_at")}
        try:
            run = handle_cleaner_done_event(engine, event)
        except ValueError as exc:
            return {"error": str(exc)}
        runs[run.run_id] = run
        return run.to_dict()
    play = get_play(play_name)
    if play is None:
        return {"error": f"unknown play: {play_name or '(none)'}"}
    run = engine.start(play, None,
                       str(payload.get("message", "")),
                       str(payload.get("property_id", "")).strip(),
                       str(payload.get("date", "")).strip())
    runs[run.run_id] = run
    return run.to_dict()


def handle_event(payload: dict, engine=ENGINE, runs=None) -> dict:
    """Deliver an event (e.g. cleaner_done) to a waiting run and
    return the run's updated dict."""
    runs = RUNS if runs is None else runs
    run = runs.get(str(payload.get("run_id", "")).strip())
    if run is None:
        return {"error": "unknown run_id"}
    event_type = str(payload.get("event", "")).strip()
    if not event_type:
        return {"error": "empty event"}
    if event_type == "camera_online" and run.play == "camera_offline":
        # The camera came back: if the offline run's outward step
        # was only held, this closes the run as resolved.
        handle_camera_online_event(engine, run, payload.get("payload") or {})
        return run.to_dict()
    engine.handle_event(run, event_type, payload.get("payload") or {})
    return run.to_dict()


def handle_get_run(run_id: str, runs=None) -> dict:
    runs = RUNS if runs is None else runs
    run = runs.get(str(run_id).strip())
    if run is None:
        return {"error": "unknown run_id"}
    return run.to_dict()


def _send_json(handler, result: dict, status: int = 200) -> None:
    body = json.dumps(result).encode()
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json")
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path.startswith("/run/"):
            result = handle_get_run(self.path[len("/run/"):])
            _send_json(self, result, 404 if "error" in result else 200)
            return
        with open(os.path.join(HERE, "index.html"), "rb") as fh:
            body = fh.read()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        if self.path not in ("/triage", "/play", "/event"):
            self.send_response(404); self.end_headers(); return
        length = int(self.headers.get("Content-Length", 0))
        try:
            payload = json.loads(self.rfile.read(length) or b"{}")
        except ValueError:
            payload = {}
        if self.path == "/triage":
            result = handle_triage(payload)
        elif self.path == "/play":
            result = handle_play(payload)
        else:
            result = handle_event(payload)
        _send_json(self, result)

    def log_message(self, *args):
        pass


if __name__ == "__main__":
    port = int(os.environ.get("JEV_SANDBOX_PORT", "8791"))
    ThreadingHTTPServer(("127.0.0.1", port), Handler).serve_forever()
