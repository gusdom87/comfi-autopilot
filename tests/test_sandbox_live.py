import importlib.util, json, os, sys, threading, urllib.request
from datetime import datetime, timedelta, timezone
from http.server import ThreadingHTTPServer

ROOT = os.path.join(os.path.dirname(__file__), "..")
sys.path.insert(0, os.path.join(ROOT, "src"))


def load_mod(name, rel):
    spec = importlib.util.spec_from_file_location(name, os.path.join(ROOT, rel))
    mod = importlib.util.module_from_spec(spec); spec.loader.exec_module(mod)
    return mod


srv = load_mod("sandbox_server_live", "sandbox/server.py")
BASE = datetime(2026, 10, 28, 8, 0, tzinfo=timezone.utc)
ESC = "muse-spark-1.3 -> muse-wonder-1.0"


def _decided(i):
    # Mixed offsets on purpose: a plain string sort would misorder them.
    t = BASE + timedelta(minutes=i)
    if i % 5 == 0:
        return t.astimezone(timezone(timedelta(hours=-5))).isoformat()
    if i % 7 == 0:
        return t.strftime("%Y-%m-%dT%H:%M:%SZ")
    return t.isoformat()


def _row(i, eng, gus=None, **kw):
    r = {"row_id": f"r{i:02d}", "decided_at": _decided(i),
         "message_at": "2026-10-26T14:55:39+00:00", "property": "Fixture Condo",
         "listing": "Fixture Condo", "platform": "airbnb",
         "reservation_code": f"HMFIX{i:05d}", "guest": "", "property_id": "pid",
         "reservation_id": "rid", "repo_commit": "abc", "message_text": "Thanks",
         "engine_category": eng, "confidence": 0.9, "tier": "muse-spark-1.3",
         "route": "none", "reason": "r", "draft": "", "cost_usd": 0.01,
         "answered_by_human": False, "host_outcome": "", "host_reply_latency_min": None,
         "verdict": "", "verdict_basis": "", "never_graduate": False,
         "never_graduate_topics": []}
    if gus is not None:
        r["gus_category"] = gus
    r.update(kw)
    return r


# Labeled rows in decided order. Window 1 (first 20 labeled): the three
# disagreements come FIRST, 17/20 agree. Window 2 (the rest): 2/3 agree.
W1 = ([("Act", "Confirm")] * 2 + [("Ignore", "Confirm")] + [("Ignore", "Ignore")] * 8
      + [("Confirm", "Confirm")] * 5 + [("Act", "Act")] * 4)
W2 = [("Confirm", "Confirm"), ("Ignore", "Ignore"), ("Act", "Confirm")]
UNLABELED_AT = {2: ("Act", None), 9: ("Ignore", None), 15: ("Confirm", ""), 26: ("Ignore", None)}


def fixture_rows():
    labeled = iter(W1 + W2)
    rows = []
    for i in range(27):
        eng, gus = UNLABELED_AT[i] if i in UNLABELED_AT else next(labeled)
        rows.append(_row(i, eng, gus))
    rows[5]["tier"] = ESC
    rows[24]["tier"] = ESC
    rows[25]["never_graduate"] = True
    rows[25]["never_graduate_topics"] = ["money"]
    rows[3]["cost_usd"] = None                       # a missing cost counts as 0
    rows[26]["message_text"] = "Hi there,\nCan we check in at 3?\nThanks\nJordan"
    rows[26]["draft"] = "Hi Jordan, let me check with the cleaner."
    return rows


def write_ledger(path, rows, bad_lines=0):
    # Reverse file order: the aggregation must sort by decided_at itself.
    with open(path, "w") as fh:
        for r in reversed(rows):
            fh.write(json.dumps(r) + "\n")
        fh.write("not json\n" * bad_lines)
    return str(path)


def write_run(runs_dir, name, n, confirm_act, tier_paths):
    d = runs_dir / name
    d.mkdir(parents=True)
    stats = {"n": n, "accuracy": 0.8, "decisive_accuracy": 0.85, "decisive_n": 50,
             "total_cost_usd": 1.5, "high_conf_accuracy": 0.75, "high_conf_n": 80,
             "mean_cost_usd": 0.015,
             "by_category": {"Confirm": {"n": 37, "accuracy": 0.8}},
             "confusion": {"Confirm": {"Confirm": 30, "Act": confirm_act, "Ignore": 6},
                           "Act": {"Confirm": 9, "Act": 17, "Ignore": 3},
                           "Ignore": {"Confirm": 4, "Act": 1, "Ignore": 30}}}
    (d / "stats.json").write_text(json.dumps(stats))
    with open(d / "results.jsonl", "w") as fh:
        for tp in tier_paths:
            fh.write(json.dumps({} if tp is None else {"tier_path": tp}) + "\n")


def fixture_runs(tmp_path):
    runs = tmp_path / "runs"
    write_run(runs, "final-107-fixed3", 101, 1, [ESC, ESC, "muse-spark-1.3", ESC])
    write_run(runs, "final-107-open3", 100, 2,
              ["meta-models/muse-spark-open-1026 -> muse-wonder-1.0", "", None])
    return str(runs)


def test_shadow_totals_windows_and_categories(tmp_path):
    ledger = write_ledger(tmp_path / "ledger.jsonl", fixture_rows(), bad_lines=1)
    d = srv.handle_shadow_data(ledger_path=ledger, runs_dir=fixture_runs(tmp_path))
    assert d["ledger_ok"] is True
    assert d["errors"] == ["shadow ledger: 1 unreadable line(s) skipped"]
    t = d["totals"]
    assert t["rows"] == 27
    assert t["by_category"] == {"Act": 8, "Confirm": 7, "Ignore": 12}
    assert t["labeled"] == 23                        # "" is not a label
    assert t["labeled_agree"] == 19 and t["labeled_agree_pct"] == 82.6
    assert t["escalations"] == 2
    assert t["cost_usd"] == 0.26
    assert t["never_graduate"] == 1
    w1, w2 = d["windows"]["window_1"], d["windows"]["window_2"]
    assert (w1["n"], w1["agree"], w1["pct"], w1["partial"]) == (20, 17, 85.0, False)
    assert (w2["n"], w2["agree"], w2["pct"], w2["size"]) == (3, 2, 66.7, 20)
    assert d["by_category"] == {
        "Act": {"called": 7, "agreed": 4, "pct": 57.1},
        "Confirm": {"called": 6, "agreed": 6, "pct": 100.0},
        "Ignore": {"called": 10, "agreed": 9, "pct": 90.0}}
    # rows come back in decided order, whatever the file order or offset format
    assert [r["row_id"] for r in d["rows"]] == [f"r{i:02d}" for i in range(27)]


def test_window_one_partial_when_fewer_than_twenty_labeled(tmp_path):
    rows = [_row(i, "Ignore", "Ignore" if i % 4 else "Confirm") for i in range(12)]
    d = srv.handle_shadow_data(ledger_path=write_ledger(tmp_path / "l.jsonl", rows),
                               runs_dir=fixture_runs(tmp_path))
    w1, w2 = d["windows"]["window_1"], d["windows"]["window_2"]
    assert (w1["n"], w1["agree"], w1["pct"], w1["partial"]) == (12, 9, 75.0, True)
    assert (w2["n"], w2["agree"], w2["pct"]) == (0, 0, None)


def test_eval_confirm_to_act_and_escalations(tmp_path):
    ledger = write_ledger(tmp_path / "ledger.jsonl", fixture_rows())
    d = srv.handle_shadow_data(ledger_path=ledger, runs_dir=fixture_runs(tmp_path))
    fixed, open_ = d["evals"]
    assert (fixed["name"], fixed["policy"], fixed["lane"]) == ("final-107-fixed3", "escalate", "A")
    assert (open_["name"], open_["policy"], open_["lane"]) == ("final-107-open3", "open_escalate", "B")
    assert fixed["confirm_to_act"] == 1 and open_["confirm_to_act"] == 2
    assert fixed["escalations"] == 3 and open_["escalations"] == 1
    assert d["zero_dangerous"] == 3 and d["zero_dangerous_n"] == 201
    assert set(fixed["stats"]) == set(srv.STATS_KEYS)   # mean_cost_usd not passed
    assert fixed["stats"]["confusion"]["Confirm"]["Act"] == 1
    assert d["escalation_threshold"] == 0.75


def test_one_eval_missing_withholds_the_zero(tmp_path):
    runs = tmp_path / "runs"
    write_run(runs, "final-107-fixed3", 101, 0, [ESC])
    d = srv.handle_shadow_data(ledger_path=write_ledger(tmp_path / "l.jsonl", fixture_rows()),
                               runs_dir=str(runs))
    assert [e["name"] for e in d["evals"]] == ["final-107-fixed3"]
    assert d["zero_dangerous"] is None and d["zero_dangerous_n"] is None
    assert any(e.startswith("final-107-open3: stats unreadable") for e in d["errors"])


def test_missing_sources_degrade_without_failing(tmp_path):
    d = srv.handle_shadow_data(ledger_path=str(tmp_path / "nope.jsonl"),
                               runs_dir=str(tmp_path / "no-runs"))
    assert d["ledger_ok"] is False and d["rows"] == []
    assert d["totals"]["rows"] == 0 and d["totals"]["labeled_agree_pct"] is None
    assert d["windows"]["window_1"]["partial"] is True
    assert d["evals"] == [] and d["zero_dangerous"] is None
    assert "shadow ledger: not found" in d["errors"] and len(d["errors"]) == 3
    # an empty ledger file is available, just empty
    empty = tmp_path / "empty.jsonl"; empty.write_text("")
    assert srv.handle_shadow_data(ledger_path=str(empty))["ledger_ok"] is True


def test_guest_names_and_ids_never_leave_the_server(tmp_path):
    d = srv.handle_shadow_data(ledger_path=write_ledger(tmp_path / "l.jsonl", fixture_rows()),
                               runs_dir=fixture_runs(tmp_path))
    for r in d["rows"]:
        assert not {"guest", "property_id", "reservation_id", "repo_commit"} & set(r)
    last = d["rows"][-1]
    assert last["message_text"] == "Hi there,\nCan we check in at 3?\nThanks\n[name]"
    assert last["draft"] == "Hi [name], let me check with the cleaner."
    assert srv.signoff_names("Thanks, see you soon!\n\n- Sam Lee") == ["Sam", "Lee"]
    assert srv.signoff_names("Great stay\nThank you") == []
    assert srv.signoff_names("Got it\nThanks") == []
    assert srv.signoff_names("Thanks Jordan") == []      # one line: not a sign-off block


def test_real_eval_runs_back_the_zero_label(tmp_path):
    # The page's ZERO label is fixed text ("... (202 decisions)"); this pins it
    # to the measured runs in the repo.
    d = srv.handle_shadow_data(ledger_path=str(tmp_path / "none.jsonl"))
    assert [e["name"] for e in d["evals"]] == ["final-107-fixed3", "final-107-open3"]
    assert d["zero_dangerous"] == 0 and d["zero_dangerous_n"] == 202
    assert [e["escalations"] for e in d["evals"]] == [14, 9]


def test_http_shadow_data_and_page_section(tmp_path, monkeypatch):
    monkeypatch.setenv("JEV_SHADOW_LEDGER", write_ledger(tmp_path / "l.jsonl", fixture_rows()))
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), srv.Handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{httpd.server_address[1]}"
    try:
        with urllib.request.urlopen(base + "/shadow-data?t=1") as resp:
            assert resp.status == 200
            d = json.load(resp)
        assert d["totals"]["rows"] == 27 and d["zero_dangerous"] == 0
        monkeypatch.setenv("JEV_SHADOW_LEDGER", str(tmp_path / "gone.jsonl"))
        with urllib.request.urlopen(base + "/shadow-data") as resp:
            assert resp.status == 200 and json.load(resp)["ledger_ok"] is False
        with urllib.request.urlopen(base + "/") as resp:
            html = resp.read().decode()
    finally:
        httpd.shutdown()
    # The section sits under the masthead, before Triage, with a rail anchor first.
    assert html.index("</header>") < html.index('id="live"') < html.index('id="triage"')
    assert html.index('href="#live"') < html.index('href="#triage"')
    for text in (
        "Dangerous false actions — truth Confirm, predicted Act — across both "
        "measured eval runs (202 decisions)",
        "Replay of the live shadow ledger. Real decisions on real guest messages. "
        "Nothing was sent — the engine is graded against what the host actually did.",
        "A category earns send rights only at ~95% agreement across two consecutive "
        "20-message windows. Money and safety topics never graduate — a human always "
        "decides those.",
        "Below the 95% bar — still in shadow. Every category is held at P1 approve-first.",
        "Money / safety flags in the live ledger so far:",
        "fetch('/shadow-data'"):
        assert text in html
