"""Judge sandbox server (stdlib only). Serves the page, POST /triage,
the plays endpoints (POST /play, POST /event, GET /run/<id>), and
GET /shadow-data (the live shadow ledger and the two measured eval
runs, aggregated read-only per request).
All model calls happen server-side; the API key never reaches the
browser. Play runs live in memory, on the staged fixture world; every
play effect is simulated or held for approval - nothing real is sent.
Run: JEV_SANDBOX_PORT=8791 python3 sandbox/server.py"""
import hashlib
import json
import os
import re
import sys
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
from jev.autonomy import load_ledger, right_for  # noqa: E402
from jev.plays import FixtureContext, PlayEngine, default_fixture_data  # noqa: E402
from jev.plays import get as get_play  # noqa: E402
from jev.plays.camera_check import (  # noqa: E402
    handle_camera_event, handle_camera_offline_event,
    handle_camera_online_event, handle_camera_upkeep_event,
    handle_guest_lockout_event, handle_guest_unsafe_event,
    handle_morning_check_event, handle_offline_burst,
    handle_post_turnover_event, handle_sunset_check_event,
    handle_vendor_window_event)
from jev.plays.cleaner_done import handle_cleaner_done_event  # noqa: E402
from jev.tiers import ESCALATION_THRESHOLD  # noqa: E402
from jev.tiers import decide as _decide  # noqa: E402
from jev.types import FAIL_SAFE  # noqa: E402

HERE = os.path.dirname(__file__)
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# The run of record's graded eval ledger: the sandbox's default book
# when neither a ledger_path argument nor JEV_LEDGER is given.
DEFAULT_LEDGER = os.path.join(ROOT, "eval", "runs", "final-107-live",
                              "ledger.json")


def _ledger_path(ledger_path: str = "") -> str:
    return ledger_path or os.environ.get("JEV_LEDGER", "") or DEFAULT_LEDGER


def handle_triage(payload: dict, decide_fn=_decide, ledger_path: str = "") -> dict:
    message = str(payload.get("message", "")).strip()
    if not message:
        return {"error": "empty message"}
    context = {"property": str(payload.get("property", ""))}
    d = decide_fn("escalate", message, context)
    ledger = load_ledger(_ledger_path(ledger_path))
    return {
        "category": d.category.value,
        "confidence": d.confidence,
        "draft": d.draft,
        "route": d.route,
        "reason": d.reason,
        "tier_path": d.tier_path,
        "cost_usd": round(d.cost_usd, 6),
        "autonomy_right": right_for(d.category.value, ledger, d.confidence),
        "failsafe": d.reason == FAIL_SAFE.reason,
    }


# -- plays -------------------------------------------------------------
# One engine over the staged fixture world, shared by every request.
# It reads the same graded eval ledger (run of record) as triage. No
# category has cleared the ladder and the play action keys carry no
# graded stats, so every action right is P1 (approve_first): drafts
# are produced and steps are HELD, exactly what the demo needs to
# show. Runs are kept in memory by run_id.
ENGINE = PlayEngine(FixtureContext(default_fixture_data()),
                    ledger=load_ledger(_ledger_path()))
RUNS: dict = {}


def handle_play(payload: dict, engine=ENGINE, runs=None) -> dict:
    """Start a play run. The message-started plays (early_checkin,
    in_stay_issue, late_checkout) start from a scenario message +
    property + date through the generic path at the bottom;
    cleaner_done starts by delivering its event (the cleaner's finish
    is the trigger). Returns the run's dict."""
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
    if play_name == "guest_lockout":
        event = {k: str(payload.get(k, "")).strip()
                 for k in ("property_id", "date", "camera", "ts",
                           "frame_id")}
        try:
            run = handle_guest_lockout_event(engine, event)
        except ValueError as exc:
            return {"error": str(exc)}
        runs[run.run_id] = run
        return run.to_dict()
    if play_name == "guest_unsafe":
        event = {k: str(payload.get(k, "")).strip()
                 for k in ("property_id", "date", "kind")}
        try:
            run = handle_guest_unsafe_event(engine, event)
        except ValueError as exc:
            return {"error": str(exc)}
        runs[run.run_id] = run
        return run.to_dict()
    if play_name == "camera_sunset_check":
        event = {k: str(payload.get(k, "")).strip()
                 for k in ("property_id", "date", "kind", "camera",
                           "ts", "frame_id")}
        try:
            run = handle_sunset_check_event(engine, event)
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
    if play_name == "camera_morning_check":
        event = {k: str(payload.get(k, "")).strip()
                 for k in ("property_id", "date", "kind", "camera",
                           "ts", "frame_id")}
        try:
            run = handle_morning_check_event(engine, event)
        except ValueError as exc:
            return {"error": str(exc)}
        runs[run.run_id] = run
        return run.to_dict()
    if play_name == "camera_upkeep":
        event = {k: str(payload.get(k, "")).strip()
                 for k in ("date", "kind")}
        try:
            run = handle_camera_upkeep_event(engine, event)
        except ValueError as exc:
            return {"error": str(exc)}
        runs[run.run_id] = run
        return run.to_dict()
    if play_name == "camera_vendor_proof":
        # The vendor's stated window starts the run; it then waits
        # for the window to close (POST /event,
        # vendor_window_closed), which checks the frames inside it.
        event = {k: str(payload.get(k, "")).strip()
                 for k in ("property_id", "date", "vendor",
                           "window_start", "window_end", "camera")}
        try:
            run = handle_vendor_window_event(engine, event)
        except ValueError as exc:
            return {"error": str(exc)}
        runs[run.run_id] = run
        return run.to_dict()
    if play_name == "camera_outage_cluster":
        # A burst of camera_offline events: each runs the ordinary
        # offline play, the correlation layer evaluates the burst,
        # and a formed cluster adds its single area-outage run.
        events = payload.get("events")
        if not isinstance(events, list) or not events:
            return {"error": "camera_outage_cluster needs a "
                             "non-empty events list"}
        try:
            out = handle_offline_burst(engine, events)
        except ValueError as exc:
            return {"error": str(exc)}
        for r in out["runs"] + out["cluster_runs"]:
            runs[r.run_id] = r
        result = {"runs": [r.to_dict() for r in out["runs"]],
                  "cluster": out["cluster"], "cluster_run": None,
                  "clusters": out["clusters"],
                  "cluster_runs": [r.to_dict() for r in out["cluster_runs"]]}
        if out["cluster_run"] is not None:
            result["cluster_run"] = out["cluster_run"].to_dict()
        return result
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


# -- live shadow data --------------------------------------------------
# GET /shadow-data: the live shadow ledger (real engine decisions on
# real guest messages, graded against what the host did; nothing was
# sent) and the two measured eval runs. Computed fresh per request from
# the files, read-only, with property names, towns and reservation codes
# anonymized for display (see the display anonymizer below). A missing
# or unreadable source becomes an entry in `errors`; the endpoint never
# fails.
SHADOW_LEDGER_DEFAULT = "/home/hatch/workspace/hackathon/shadow-live/ledger.jsonl"
EVAL_RUNS_DIR = os.path.join(HERE, "..", "eval", "runs")
EVAL_RUNS = (
    {"name": "final-107-fixed3", "lane": "A", "policy": "escalate",
     "first_pass": "Muse Spark (Meta Model API)",
     "escalation": "Muse Wonder"},
    {"name": "final-107-open3", "lane": "B", "policy": "open_escalate",
     "first_pass": "Muse Spark Open (Baseten, open weights)",
     "escalation": "Muse Wonder"},
)
CATEGORIES = ("Act", "Confirm", "Ignore")
WINDOW = 20
STATS_KEYS = ("accuracy", "decisive_accuracy", "decisive_n", "n",
              "total_cost_usd", "high_conf_accuracy", "high_conf_n",
              "by_category", "confusion")
# The page's fields only. The ledger's guest, property/reservation ids,
# reservation codes and repo stamps are never sent to the browser.
ROW_FIELDS = ("row_id", "decided_at", "message_at", "property", "listing",
              "platform", "message_text",
              "engine_category", "confidence", "tier", "route", "reason",
              "draft", "cost_usd", "answered_by_human", "host_outcome",
              "host_reply_latency_min", "verdict", "verdict_basis",
              "gus_category", "gus_draft_verdict", "gus_note",
              "never_graduate", "never_graduate_topics")

# A guest signing off on the message's last line ("...\n\nThanks\nSam").
# The ledger keeps no guest name by design, so a name found there is
# replaced with [name] in every text field of that row before it leaves
# the server. Closing words are not names.
_SIGNOFF_LINE = re.compile(
    r"(?:(?i:thanks|thank you|cheers|best|regards|sincerely|warmly)[,!.]*\s+"
    r"|[-–—~]\s*)?"
    r"([A-Z][a-z]+(?:[ -][A-Z][a-z]+)?)[.!]?")
_NOT_NAMES = {"thanks", "thank", "cheers", "best", "regards", "sincerely",
              "warmly", "ok", "okay", "yes", "no", "sure", "perfect",
              "great", "hello", "hi", "hey", "bye", "appreciated"}
_SCRUBBED_TEXT = ("message_text", "draft", "reason", "gus_note",
                  "verdict_basis")


def signoff_names(text: str) -> list:
    lines = [ln.strip() for ln in str(text or "").splitlines() if ln.strip()]
    if len(lines) < 2:
        return []
    m = _SIGNOFF_LINE.fullmatch(lines[-1])
    if not m:
        return []
    words = re.split(r"[ -]", m.group(1))
    return [] if any(w.lower() in _NOT_NAMES for w in words) else words


# -- display anonymizer ------------------------------------------------
# The feed is filmed for a public video, so real client property names,
# their towns and reservation codes never reach the page. Display layer
# only: the ledger on disk is read, never changed. Names follow the
# project's privacy renames (the Cedar Hollow units, Ridgeview, Maple
# Street House); the rest are invented in the same register. Keys are a
# SHA-256 digest of the Hospitable property id (or of a lowercased town
# word), so this file holds no real id and no real name. An id missing
# from the map shows as "Private listing".
PRIVATE_LISTING = "Private listing"
DISPLAY_NAMES = {
    # the four Cedar Hollow units and the Retreat
    "9fe0c1e591c0": "Garden Unit 1A",
    "20697ff16e83": "Garden Unit 1B",
    "41b0d4acb832": "Studio 2A",
    "61e407c0e7f5": "Upper Unit 2B",
    "228c8f491d3c": "Cedar Hollow Retreat",
    # client condos
    "4471f311c55f": "Ridgeview Condo",
    "854b8fda5abd": "Parkside Condo",
    # client houses (one house has two listing records, one name)
    "ef78e5cab88b": "Maple Street House",
    "fd32156908a4": "Maple Street House",
    "73bec54301dc": "Hillcrest House",
    "feee0fbb5449": "Brookside House",
    "0723734e6b75": "Willow Lane House",
    "fe640456e83a": "Briarwood House",
    "530ed157216a": "Linden House",
}
# Towns the listings sit in, as a guest might type them.
PLACE_NAMES = {
    "3e7a475906d0": "Cedar Hollow",
    "64ed1bc1f966": "Ridgeview",
    "fb4a9aa73dae": "[area]",
    "8ed938e65018": "[area]",
    "736189d6b69b": "[area]",
}
CODE_MASK = "[code]"
# Airbnb (HM + 8), Vrbo (HA-...) and Hospitable's own (VRBO-/HOST-...) codes.
CODE_SHAPE = re.compile(r"\b(?:HM[A-Z0-9]{8}|(?:HA|HOST|VRBO)-[A-Z0-9]{6,})\b")


def digest(value) -> str:
    return hashlib.sha256(str(value).encode()).hexdigest()[:12]


def display_name(property_id) -> str:
    return DISPLAY_NAMES.get(digest(property_id or ""), PRIVATE_LISTING)


def _name_pattern(name: str):
    """A real name as a guest or note might type it: apostrophe straight,
    curly or left out, parentheses optional, space and hyphen alike."""
    parts = []
    for ch in name:
        if ch in "'’":
            parts.append("['’]?")
        elif ch in "()":
            parts.append(re.escape(ch) + "?")
        elif ch in " -":
            parts.append(r"[\s-]*")
        else:
            parts.append(re.escape(ch))
    return re.compile(r"(?<!\w)%s(?!\w)" % "".join(parts), re.IGNORECASE)


def build_anonymizer(rows: list):
    """A text scrub built from the ledger's own property names and
    reservation codes, so no list of real names lives in this file."""
    shown_for, codes = {}, set()
    for r in rows:
        shown = display_name(r.get("property_id"))
        for k in ("property", "listing"):
            full = str(r.get(k) or "").strip()
            for name in (full, full.split("|")[0].strip()):
                if len(name) >= 4:
                    shown_for.setdefault(name, shown)
        if str(r.get("reservation_code") or "").strip():
            codes.add(str(r["reservation_code"]).strip())
    names = [(_name_pattern(n), s)
             for n, s in sorted(shown_for.items(), key=lambda kv: -len(kv[0]))]
    code_re = re.compile("|".join(map(re.escape, sorted(codes, key=len, reverse=True)))
                         ) if codes else None

    def anonymize(text: str) -> str:
        for pattern, shown in names:
            text = pattern.sub(shown, text)
        if code_re:
            text = code_re.sub(CODE_MASK, text)
        text = CODE_SHAPE.sub(CODE_MASK, text)
        return re.sub(r"[A-Za-z]+",
                      lambda m: PLACE_NAMES.get(digest(m.group().lower()), m.group()),
                      text)
    return anonymize


def scrub_row(row: dict, anonymize=None) -> dict:
    out = {k: row.get(k) for k in ROW_FIELDS if k in row}
    names = signoff_names(out.get("message_text", ""))
    name_re = re.compile(r"\b(?:%s)\b" % "|".join(map(re.escape, names)),
                         re.IGNORECASE) if names else None
    anonymize = anonymize or build_anonymizer([row])
    for k in _SCRUBBED_TEXT:
        if isinstance(out.get(k), str):
            if name_re:
                out[k] = name_re.sub("[name]", out[k])
            out[k] = anonymize(out[k])
    out["property"] = out["listing"] = display_name(row.get("property_id"))
    return out


def _ts(value) -> float:
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).timestamp()
    except ValueError:
        return float("inf")


def _pct(agree: int, n: int):
    return round(100.0 * agree / n, 1) if n else None


def _num(value) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def _labeled(row: dict) -> bool:
    return bool(row.get("gus_category"))


def _agrees(row: dict) -> bool:
    return row.get("gus_category") == row.get("engine_category")


def _read_ledger(path: str, errors: list) -> list:
    try:
        with open(path, encoding="utf-8") as fh:
            lines = fh.readlines()
    except FileNotFoundError:
        errors.append("shadow ledger: not found")
        return []
    except (OSError, UnicodeDecodeError) as exc:
        errors.append(f"shadow ledger: unreadable ({type(exc).__name__})")
        return []
    rows, bad = [], 0
    for line in lines:
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except ValueError:
            bad += 1
            continue
        if isinstance(row, dict):
            rows.append(row)
        else:
            bad += 1
    if bad:
        errors.append(f"shadow ledger: {bad} unreadable line(s) skipped")
    # Stable: rows with the same decided_at keep their file order.
    return sorted(rows, key=lambda r: _ts(r.get("decided_at")))


def _window(rows: list) -> dict:
    n = len(rows)
    agree = sum(_agrees(r) for r in rows)
    return {"n": n, "size": WINDOW, "agree": agree, "pct": _pct(agree, n)}


def _ledger_summary(rows: list) -> dict:
    labeled = [r for r in rows if _labeled(r)]
    agree = sum(_agrees(r) for r in labeled)
    totals = {
        "rows": len(rows),
        "by_category": {c: sum(r.get("engine_category") == c for r in rows)
                        for c in CATEGORIES},
        "labeled": len(labeled),
        "labeled_agree": agree,
        "labeled_agree_pct": _pct(agree, len(labeled)),
        "escalations": sum("->" in str(r.get("tier") or "") for r in rows),
        "cost_usd": round(sum(_num(r.get("cost_usd")) for r in rows), 6),
        "never_graduate": sum(bool(r.get("never_graduate")) for r in rows),
        # Zero by construction: the shadow runner has no send path
        # (shadow_live.py, "NEVER-SEND DESIGN"), so no row was acted on,
        # money and safety rows included.
        "money_safety_acted_alone": 0,
    }
    w1 = _window(labeled[:WINDOW])
    w1["partial"] = w1["n"] < WINDOW
    windows = {"window_1": w1, "window_2": _window(labeled[WINDOW:])}
    by_category = {}
    for c in CATEGORIES:
        called = [r for r in labeled if r.get("engine_category") == c]
        agreed = sum(_agrees(r) for r in called)
        by_category[c] = {"called": len(called), "agreed": agreed,
                          "pct": _pct(agreed, len(called))}
    return {"totals": totals, "windows": windows, "by_category": by_category}


def _read_eval(spec: dict, runs_dir: str, errors: list):
    run_dir = os.path.join(runs_dir, spec["name"])
    try:
        with open(os.path.join(run_dir, "stats.json"), encoding="utf-8") as fh:
            stats = json.load(fh)
        confirm_to_act = int(stats["confusion"]["Confirm"]["Act"])
    except (OSError, ValueError, TypeError, KeyError, AttributeError) as exc:
        errors.append(f"{spec['name']}: stats unreadable ({type(exc).__name__})")
        return None
    escalations = None
    try:
        with open(os.path.join(run_dir, "results.jsonl"), encoding="utf-8") as fh:
            escalations = sum("->" in str(json.loads(ln).get("tier_path") or "")
                              for ln in fh if ln.strip())
    except (OSError, ValueError, AttributeError) as exc:
        errors.append(f"{spec['name']}: results unreadable ({type(exc).__name__})")
    return dict(spec, stats={k: stats.get(k) for k in STATS_KEYS},
                escalations=escalations, confirm_to_act=confirm_to_act)


def handle_shadow_data(ledger_path: str = "", runs_dir: str = "") -> dict:
    errors: list = []
    path = ledger_path or os.environ.get("JEV_SHADOW_LEDGER") or SHADOW_LEDGER_DEFAULT
    n_errors = len(errors)
    rows = _read_ledger(path, errors)
    ledger_ok = rows or not any(e.startswith(("shadow ledger: not found",
                                              "shadow ledger: unreadable"))
                                for e in errors[n_errors:])
    anonymize = build_anonymizer(rows)
    out = {"ledger_ok": bool(ledger_ok),
           "rows": [scrub_row(r, anonymize) for r in rows],
           **_ledger_summary(rows)}
    evals = [e for e in (_read_eval(s, runs_dir or EVAL_RUNS_DIR, errors)
                         for s in EVAL_RUNS) if e is not None]
    both = len(evals) == len(EVAL_RUNS)
    out["evals"] = evals
    # The ZERO is only claimed across both runs; with one missing it is None.
    out["zero_dangerous"] = sum(e["confirm_to_act"] for e in evals) if both else None
    out["zero_dangerous_n"] = sum(int(e["stats"].get("n") or 0) for e in evals) if both else None
    out["escalation_threshold"] = ESCALATION_THRESHOLD
    out["errors"] = errors
    return out


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
        if self.path.split("?")[0] == "/shadow-data":
            try:
                result = handle_shadow_data()
            except Exception as exc:  # never a 500: the page degrades quietly
                result = {"ledger_ok": False, "rows": [], "evals": [],
                          "zero_dangerous": None,
                          "errors": [f"shadow data: failed ({type(exc).__name__})"]}
            _send_json(self, result)
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
