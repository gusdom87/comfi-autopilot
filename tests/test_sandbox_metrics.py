import csv, importlib.util, os, sys

ROOT = os.path.join(os.path.dirname(__file__), "..")
sys.path.insert(0, os.path.join(ROOT, "src"))

def load_mod(name, rel):
    spec = importlib.util.spec_from_file_location(name, os.path.join(ROOT, rel))
    mod = importlib.util.module_from_spec(spec); spec.loader.exec_module(mod)
    return mod

def test_sandbox_triage_shape_and_failsafe():
    srv = load_mod("sandbox_server", "sandbox/server.py")
    from jev.types import Decision, Category, FAIL_SAFE
    fake = Decision(Category.ACT, 0.9, draft="On it.", route="cleaner", reason="r", tier_path="m")
    out = srv.handle_triage({"message": "leak!", "property": "P"}, decide_fn=lambda p, m, c: fake)
    assert out["category"] == "Act" and out["autonomy_right"] == "approve_first" and out["failsafe"] is False
    out2 = srv.handle_triage({"message": "hi"}, decide_fn=lambda p, m, c: FAIL_SAFE)
    assert out2["failsafe"] is True and out2["category"] == "Confirm"
    html = open(os.path.join(ROOT, "sandbox", "index.html")).read()
    assert html.count("data-msg") == 4 and "pre-existing — Jev, the decision model by TypeSafe AI" in html

def test_sandbox_triage_low_confidence_gets_approve_first(tmp_path):
    srv = load_mod("sandbox_server", "sandbox/server.py")
    from jev.types import Decision, Category
    ledger = tmp_path / "ledger.json"
    ledger.write_text('{"categories": {"Ignore": {"decisive_n": 30, "decisive_accuracy": 0.96, "high_conf_accuracy": 0.99}}}')
    low = Decision(Category.IGNORE, 0.3, reason="r", tier_path="m")
    out = srv.handle_triage({"message": "ok"}, decide_fn=lambda p, m, c: low, ledger_path=str(ledger))
    assert out["autonomy_right"] == "approve_first"
    high = Decision(Category.IGNORE, 0.95, reason="r", tier_path="m")
    out = srv.handle_triage({"message": "ok"}, decide_fn=lambda p, m, c: high, ledger_path=str(ledger))
    assert out["autonomy_right"] == "send_with_log"

def test_sandbox_defaults_to_run_of_record_ledger(monkeypatch):
    monkeypatch.delenv("JEV_LEDGER", raising=False)
    srv = load_mod("sandbox_server", "sandbox/server.py")
    from jev.autonomy import load_ledger, right_for
    from jev.plays.engine import STAKES
    from jev.types import Decision, Category
    record = os.path.join(ROOT, "eval", "runs", "final-107-live", "ledger.json")
    assert os.path.samefile(srv._ledger_path(), record)
    ledger = load_ledger(record)
    assert set(ledger["categories"]) == {"Confirm", "Act", "Ignore"}
    assert srv.ENGINE.ledger == ledger
    # No category has cleared the ladder: every right is still P1.
    for cat in Category:
        for conf in (None, 0.85, 1.0):
            assert right_for(cat.value, ledger, conf) == "approve_first"
        d = Decision(cat, 0.99, reason="r", tier_path="m")
        out = srv.handle_triage({"message": "ok"}, decide_fn=lambda p, m, c: d)
        assert out["autonomy_right"] == "approve_first"
    for key in STAKES:
        assert right_for(key, ledger, 1.0) == "approve_first"

def test_metrics_math(tmp_path):
    met = load_mod("outcome_metrics", "metrics/outcome_metrics.py")
    path = tmp_path / "w.csv"
    rows = [
        {"message_id": "1", "property": "P", "received_at": "2026-10-07T10:00:00+00:00", "first_response_at": "2026-10-07T11:00:00+00:00", "category": "Act", "responded_by": "va", "dispatch_at": "2026-10-07T10:30:00+00:00"},
        {"message_id": "2", "property": "P", "received_at": "2026-10-07T10:00:00+00:00", "first_response_at": "2026-10-07T12:00:00+00:00", "category": "Act", "responded_by": "host", "dispatch_at": ""},
        {"message_id": "3", "property": "P", "received_at": "2026-10-07T10:00:00Z", "first_response_at": "2026-10-07T13:00:00Z", "category": "Act", "responded_by": "system", "dispatch_at": ""},
        {"message_id": "4", "property": "P", "received_at": "2026-10-07T10:00:00+00:00", "first_response_at": "", "category": "Act", "responded_by": "host", "dispatch_at": ""},
        {"message_id": "5", "property": "P", "received_at": "2026-10-07T10:00:00+00:00", "first_response_at": "2026-10-07T10:05:00+00:00", "category": "Confirm", "responded_by": "host", "dispatch_at": ""},
    ]
    with open(path, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0])); w.writeheader(); w.writerows(rows)
    s = met.summarize(met.load(str(path)))
    assert s["urgent_response_median_min"] == 120.0 and s["urgent_response_p90_min"] == 180.0
    assert s["urgent_missing_timestamps"] == 1
    assert abs(s["no_touch_rate"] - 0.4) < 1e-9 and s["dispatch_median_min"] == 30.0
    assert "Directional only" in met.render(s, s)
