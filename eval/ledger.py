"""Aggregate eval results into the confidence ledger that autonomy.py reads.
Usage: python ledger.py RESULTS_JSONL [MORE.jsonl ...] --out ledger.json

Per-category stats are PRECISION, grouped by the predicted category: for
category X the subset is every row the system called X, and accuracy is
how often that call matched the ground truth. Decisive rows are the
subset's Act or Ignore calls (none for Confirm, which defers to a human);
high-confidence rows are the subset's calls at confidence >= 0.85.
Why precision: autonomy.right_for() is asked about the system's own call
(the predicted category), so the ladder gates on how often the system is
right when it makes that call, not on how often a true X gets caught."""
import argparse
import json
import sys

sys.path.insert(0, ".")
from scoring import CATEGORIES


def build_ledger(rows: list[dict]) -> dict:
    acc = lambda rs: (sum(1 for r in rs if r["predicted"] == r["ground_truth"]) / len(rs)) if rs else 0.0
    cats = {}
    for cat in CATEGORIES:
        subset = [r for r in rows if r["predicted"] == cat]
        decisive = [r for r in subset if r["predicted"] in ("Act", "Ignore")]
        band = [r for r in subset if r.get("confidence", 0.0) >= 0.85]
        cats[cat] = {
            "n": len(subset), "accuracy": acc(subset),
            "decisive_n": len(decisive), "decisive_accuracy": acc(decisive),
            "high_conf_n": len(band), "high_conf_accuracy": acc(band),
        }
    return {"categories": cats}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("results", nargs="+")
    ap.add_argument("--out", required=True)
    args = ap.parse_args()
    rows = []
    for path in args.results:
        with open(path) as fh:
            rows += [json.loads(l) for l in fh if l.strip()]
    with open(args.out, "w") as fh:
        json.dump(build_ledger(rows), fh, indent=1)
    print(f"Ledger for {len(rows)} results written to {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
