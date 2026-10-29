"""Eval harness. Runs a tiering policy over the anonymized eval dataset and
writes results.jsonl, stats.json, and scorecard.md into --out.

Dataset records: {"message_preview": str, "ground_truth": "Confirm|Act|Ignore",
"property": str (pseudonym), ...}. Few-shot examples (--examples N) are drawn
from the first N records and EXCLUDED from scoring (no leakage).
Raw model responses are cached under out/cache so re-scoring is free.
Worst-case cost is estimated up front; the run refuses to start over budget.
"""
import argparse
import hashlib
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from jev.baseten_client import BasetenClient
from jev.client import MetaModelClient
from jev.config import Config
from jev.tiers import decide
from scoring import render_scorecard, score_rows

MAX_CALLS = {"spark_only": 2, "wonder_only": 2, "escalate": 3, "open_escalate": 3}
EST_IN, EST_OUT = 350, 90  # tokens, rough planning figures
PRICES = {"muse-spark-1.3": (1.25, 4.25), "muse-wonder-1.0": (4.00, 20.00)}


class CachingClient:
    def __init__(self, inner, cache_dir):
        self.inner, self.cache_dir = inner, cache_dir
        os.makedirs(cache_dir, exist_ok=True)

    def complete(self, model, system, user):
        key = hashlib.sha256(f"{model}\n{system}\n{user}".encode()).hexdigest()
        path = os.path.join(self.cache_dir, key + ".json")
        if os.path.exists(path):
            with open(path) as fh:
                return json.load(fh)
        resp = self.inner.complete(model, system, user)
        if resp is not None:
            with open(path, "w") as fh:
                json.dump(resp, fh)
        return resp


class MockClient:
    """Deterministic rule-based stand-in for plumbing tests ONLY."""
    RULES = [
        (("leak", "lock", "no hot water", "can't get in", "cannot get in", "emergency", "smoke"), "Act", 0.9),
        (("receipt", "itinerary", "notification", "confirmed your booking"), "Ignore", 0.9),
    ]

    def complete(self, model, system, user):
        low = user.lower()
        if "draft" in system.lower()[:40] or system.startswith("You draft"):
            return {"output_text": "Thanks for flagging this, we are on it.", "usage": {"input_tokens": 120, "output_tokens": 12}}
        cat, conf = "Confirm", 0.6
        for keys, c, cf in self.RULES:
            if any(k in low for k in keys):
                cat, conf = c, cf
                break
        body = json.dumps({"category": cat, "confidence": conf, "route": "host", "reason": "mock"})
        return {"output_text": body, "usage": {"input_tokens": 200, "output_tokens": 30}}


def estimate_cost(n_records: int, policy: str, config: Config) -> float:
    pin, pout = PRICES.get(config.tier_deep, (4.00, 20.00))
    per_call = (EST_IN * pin + EST_OUT * pout) / 1_000_000
    return n_records * MAX_CALLS[policy] * per_call


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dataset", required=True)
    ap.add_argument("--policy", default="escalate", choices=list(MAX_CALLS))
    ap.add_argument("--examples", type=int, default=0)
    ap.add_argument("--out", required=True)
    ap.add_argument("--budget-usd", type=float, default=40.0)
    ap.add_argument("--mock", action="store_true")
    args = ap.parse_args()

    with open(args.dataset, encoding="utf-8") as fh:
        records = [json.loads(l) for l in fh if l.strip()]
    text_of = lambda r: r.get("message") or r["message_preview"]
    examples = [
        {"message": text_of(r), "category": r["ground_truth"]}
        for r in records[: args.examples]
    ]
    scored = records[args.examples:]
    config = Config.from_env()
    est = estimate_cost(len(scored), args.policy, config)
    print(f"Records: {len(records)} total, {len(scored)} scored, {len(examples)} few-shot examples")
    print(f"Worst-case cost estimate: ${est:.2f} (budget ${args.budget_usd:.2f})")
    if est > args.budget_usd and not args.mock:
        print("REFUSING TO RUN: estimate exceeds budget.", file=sys.stderr)
        return 2

    os.makedirs(args.out, exist_ok=True)
    inner = MockClient() if args.mock else MetaModelClient(config)
    open_inner = MockClient() if args.mock else BasetenClient(config)
    # One cache dir for both APIs: the cache key includes the model name.
    client = CachingClient(inner, os.path.join(args.out, "cache"))
    open_client = CachingClient(open_inner, os.path.join(args.out, "cache"))
    rows = []
    for i, r in enumerate(scored):
        d = decide(args.policy, text_of(r), {"property": r.get("property", "")},
                   client=client, examples=examples, open_client=open_client)
        rows.append({
            "id": i,
            "ground_truth": r["ground_truth"],
            "predicted": d.category.value,
            "confidence": d.confidence,
            "correct": d.category.value == r["ground_truth"],
            "tier_path": d.tier_path,
            "cost_usd": round(d.cost_usd, 6),
        })
    with open(os.path.join(args.out, "results.jsonl"), "w") as fh:
        for row in rows:
            fh.write(json.dumps(row) + "\n")
    stats = score_rows(rows)
    with open(os.path.join(args.out, "stats.json"), "w") as fh:
        json.dump(stats, fh, indent=1)
    note = "MOCK RUN: rule-based stand-in client, not real model output." if args.mock else ""
    title = f"{args.policy}, examples={args.examples}" + (" [MOCK]" if args.mock else "")
    with open(os.path.join(args.out, "scorecard.md"), "w") as fh:
        fh.write(render_scorecard(stats, title, note))
    print(f"Accuracy {stats['accuracy']:.1%} (n={stats['n']}); decisive {stats['decisive_accuracy']:.1%} "
          f"(n={stats['decisive_n']}); high-conf {stats['high_conf_accuracy']:.1%} (n={stats['high_conf_n']})")
    print(f"Wrote results, stats, scorecard to {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
