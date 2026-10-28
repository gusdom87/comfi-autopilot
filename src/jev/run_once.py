"""CLI: triage one message and print the Decision as JSON.
Usage: python -m jev.run_once MESSAGE_FILE [--policy escalate] [--property NAME]
Also appends the decision to .decision-log/decisions.jsonl."""
import argparse
import json
import sys

from .autonomy import load_ledger, right_for
from .ledger_log import append_decision
from .tiers import decide


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("message_file")
    ap.add_argument("--policy", default="escalate")
    ap.add_argument("--property", default="")
    ap.add_argument("--ledger", default="")
    ap.add_argument("--log", default=".decision-log/decisions.jsonl")
    args = ap.parse_args()

    with open(args.message_file, encoding="utf-8") as fh:
        message = fh.read().strip()
    context = {"property": args.property} if args.property else {}
    decision = decide(args.policy, message, context)
    ledger = load_ledger(args.ledger or None)
    record = {
        "category": decision.category.value,
        "confidence": decision.confidence,
        "draft": decision.draft,
        "route": decision.route,
        "reason": decision.reason,
        "tier_path": decision.tier_path,
        "cost_usd": round(decision.cost_usd, 6),
        "autonomy_right": right_for(decision.category.value, ledger,
                                    decision.confidence),
        "policy": args.policy,
    }
    append_decision(args.log, record)
    print(json.dumps(record, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
