# Eval runs of record

All runs use the same redacted 107-message eval set (private, not in
this repo); the first 6 records are few-shot examples, so n=101 scored.
Response caches are never committed.

## final-107-live — run of record (hosted)
Policy `escalate` (Muse Spark 1.3 first pass, Muse Wonder on
low confidence, threshold 0.75), shipped prompt.
Overall 80.2%; decisive 82.3% (n=62); high-confidence 79.5% (n=78);
per-class Confirm 81.1%, Act 75.9%, Ignore 82.9%; 2 Confirm-to-Act
errors; 15 escalations; total cost $1.67.
Verified Oct 29, 2026: a key-less replay from this run's response cache
reproduced results.jsonl byte for byte, so the committed artifacts are
provably the shipped prompt's output.

## final-107-fixed3 — reverted prompt revision (kept for honesty)
Same policy with a stricter prompt that tried to route every money or
stay-change message to Confirm. Overall 77.2%; decisive 82.5% (n=57);
high-confidence 75.6% (n=86). Zero Confirm-to-Act errors, but Act recall
fell to 58.6% and it introduced a false Act of its own. Measured,
compared row by row against the run of record, and reverted: the
shipped prompt is the better one, and the money guarantee lives in the
autonomy code instead (the charge_guest hard lock).

## final-107-open3 — open-weights run under the reverted prompt
Policy `open_escalate` (Muse Spark Open on Baseten first pass, Muse
Wonder escalation, same threshold). Overall 74.3%; decisive 83.0%
(n=53); high-confidence 76.2% (n=84); zero false Acts; cost $1.16.
Kept as the record of the open-weights tier under that prompt; the
shipped-prompt open run is final-107-open-a.

## final-107-open-a — open-weights run under the shipped prompt
Policy `open_escalate`, shipped prompt, run Oct 29, 2026. Overall
81.2%; decisive 79.4% (n=68); high-confidence 81.7% (n=82); per-class
Confirm 75.7%, Act 86.2%, Ignore 82.9%; 5 false Acts (3 Confirm-to-Act);
13 escalations; zero fail-safes; cost $1.42. A point ahead of the
hosted run overall and far ahead on urgent recall, at lower cost; the
trade is more over-acting, which approve-first absorbs.

Note on ledgers: ledger.json files were regenerated Oct 29, 2026 under
precision semantics (grouped by predicted category, which is what the
autonomy ladder actually gates on). The earlier files grouped by ground
truth. Outcomes are unchanged: every category sits at approve-first
(P1) in every run.
