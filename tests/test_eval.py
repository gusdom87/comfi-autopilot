import json, os, subprocess, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "eval"))
from ledger import build_ledger
from scoring import score_rows


def row(gt, pred, conf=0.9, cost=0.001):
    return {"ground_truth": gt, "predicted": pred, "confidence": conf, "cost_usd": cost}


def test_scoring_math():
    rows = [row("Act", "Act"), row("Act", "Confirm"), row("Ignore", "Ignore", 0.7),
            row("Confirm", "Confirm", 0.95), row("Ignore", "Act")]
    s = score_rows(rows)
    assert s["n"] == 5 and abs(s["accuracy"] - 0.6) < 1e-9
    assert s["decisive_n"] == 3 and abs(s["decisive_accuracy"] - 2 / 3) < 1e-9
    assert s["high_conf_n"] == 4 and abs(s["high_conf_accuracy"] - 0.5) < 1e-9
    assert s["confusion"]["Act"]["Confirm"] == 1 and s["confusion"]["Ignore"]["Act"] == 1


def test_ledger_groups_by_predicted_category():
    # A true Act the system called Ignore is a wrong Ignore call: it
    # counts against Ignore's precision and never appears under Act.
    cats = build_ledger([row("Act", "Ignore")])["categories"]
    assert cats["Ignore"]["n"] == 1 and cats["Ignore"]["decisive_n"] == 1
    assert cats["Ignore"]["accuracy"] == 0.0
    assert cats["Ignore"]["decisive_accuracy"] == 0.0
    assert cats["Act"]["n"] == 0 and cats["Act"]["decisive_n"] == 0


def test_ledger_stats_are_precision_within_the_call():
    rows = [row("Act", "Act", 0.9), row("Act", "Act", 0.7), row("Confirm", "Act", 0.95),
            row("Ignore", "Ignore", 0.9), row("Act", "Ignore", 0.9),
            row("Confirm", "Confirm", 0.9), row("Act", "Confirm", 0.6)]
    cats = build_ledger(rows)["categories"]
    act = cats["Act"]
    assert act["n"] == 3 and act["decisive_n"] == 3
    assert abs(act["accuracy"] - 2 / 3) < 1e-9 and abs(act["decisive_accuracy"] - 2 / 3) < 1e-9
    assert act["high_conf_n"] == 2 and abs(act["high_conf_accuracy"] - 0.5) < 1e-9
    ign = cats["Ignore"]
    assert ign["n"] == 2 and ign["decisive_n"] == 2 and abs(ign["decisive_accuracy"] - 0.5) < 1e-9
    assert ign["high_conf_n"] == 2 and abs(ign["high_conf_accuracy"] - 0.5) < 1e-9
    # Confirm defers to a human, so it never has decisive rows.
    con = cats["Confirm"]
    assert con["n"] == 2 and abs(con["accuracy"] - 0.5) < 1e-9
    assert con["decisive_n"] == 0 and con["decisive_accuracy"] == 0.0
    assert con["high_conf_n"] == 1 and con["high_conf_accuracy"] == 1.0
    assert set(act) == {"n", "accuracy", "decisive_n", "decisive_accuracy",
                        "high_conf_n", "high_conf_accuracy"}


def test_mock_run_end_to_end(tmp_path):
    ds = tmp_path / "ds.jsonl"
    recs = [
        {"message_preview": "There is a leak under the sink", "ground_truth": "Act", "property": "PROP_01"},
        {"message_preview": "Here is your receipt for the stay", "ground_truth": "Ignore", "property": "PROP_01"},
        {"message_preview": "Can we bring a second car?", "ground_truth": "Confirm", "property": "PROP_02"},
        {"message_preview": "We can't get in, the lock code fails", "ground_truth": "Act", "property": "PROP_02"},
        {"message_preview": "Is early check-in possible?", "ground_truth": "Confirm", "property": "PROP_01"},
    ]
    ds.write_text("\n".join(json.dumps(r) for r in recs))
    out = tmp_path / "out"
    repo = os.path.join(os.path.dirname(__file__), "..")
    r = subprocess.run([sys.executable, os.path.join(repo, "eval", "run_eval.py"),
                        "--dataset", str(ds), "--policy", "escalate", "--mock",
                        "--out", str(out)], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    assert (out / "scorecard.md").exists() and (out / "results.jsonl").exists()
    stats = json.loads((out / "stats.json").read_text())
    assert stats["n"] == 5


def test_mock_run_open_escalate_records_policy_and_models(tmp_path):
    ds = tmp_path / "ds.jsonl"
    recs = [
        {"message_preview": "There is a leak under the sink", "ground_truth": "Act", "property": "PROP_01"},
        {"message_preview": "Can we bring a second car?", "ground_truth": "Confirm", "property": "PROP_02"},
    ]
    ds.write_text("\n".join(json.dumps(r) for r in recs))
    out = tmp_path / "out"
    repo = os.path.join(os.path.dirname(__file__), "..")
    open_model = "meta-models/muse-spark-open-1026"
    env = {**os.environ, "BASETEN_MODEL_OPEN": open_model, "META_MODEL_TIER_DEEP": "muse-wonder-1.0"}
    r = subprocess.run([sys.executable, os.path.join(repo, "eval", "run_eval.py"),
                        "--dataset", str(ds), "--policy", "open_escalate", "--mock",
                        "--out", str(out)], capture_output=True, text=True, env=env)
    assert r.returncode == 0, r.stderr
    assert "# Scorecard — open_escalate" in (out / "scorecard.md").read_text()
    rows = [json.loads(l) for l in (out / "results.jsonl").read_text().splitlines()]
    assert rows[0]["tier_path"] == open_model                          # 0.9 stands
    assert rows[1]["tier_path"] == open_model + " -> muse-wonder-1.0"  # 0.6 escalates
