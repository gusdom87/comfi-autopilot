"""Graduated autonomy: send rights are earned from the graded record,
never assumed. The ledger (eval/ledger.py output) holds per-category stats.

Ladder:
  P1 approve_first        - default for every category
  P2 send_with_log        - n >= 25 decisive samples, decisive accuracy
                            >= 0.95, and high-confidence accuracy >= 0.98
  P3 send_with_spotcheck  - n >= 75, decisive accuracy >= 0.97,
                            high-confidence accuracy >= 0.98
Ignore-category items never send anything, so their right is about
auto-closing, gated by the same thresholds.
Charging a guest (charge_guest) is never on the ladder: it always waits
for a human, whatever the ledger says.

Per-decision floor: a right earned on aggregate stats never covers a
single decision below HIGH_CONF (the eval's high-confidence band).
Pass the decision's confidence and anything under the floor is P1,
whatever the ledger says.
"""
import json

P1, P2, P3 = "approve_first", "send_with_log", "send_with_spotcheck"
HIGH_CONF = 0.85    # same band as eval/scoring.py HIGH_CONF
NEVER_EARNED = frozenset({"charge_guest"})


def load_ledger(path: str | None) -> dict:
    if not path:
        return {}
    try:
        with open(path) as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return {}


def right_for(category: str, ledger: dict,
              confidence: float | None = None) -> str:
    if category in NEVER_EARNED:
        return P1
    if confidence is not None and confidence < HIGH_CONF:
        return P1
    stats = (ledger or {}).get("categories", {}).get(category, {})
    n = int(stats.get("decisive_n", 0))
    acc = float(stats.get("decisive_accuracy", 0.0))
    hi = float(stats.get("high_conf_accuracy", 0.0))
    if n >= 75 and acc >= 0.97 and hi >= 0.98:
        return P3
    if n >= 25 and acc >= 0.95 and hi >= 0.98:
        return P2
    return P1
