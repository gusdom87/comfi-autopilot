# Eval runs of record

All runs use the same redacted 107-message eval set (private, not in
this repo); the first 6 records are few-shot examples, so n=101 scored.
Response caches are never committed.

The shipped engine always applies the stay-change/money guard
(`tiers.decide` wraps `change_guard.apply_guard`), so the guarded runs
are how the shipped code behaves: final-107-guard for the hosted lane,
ablation-2026-10-31-open for the open-weights lane. final-107-live is
kept as the model's raw record without the guard.

## final-107-live: the model's raw record (hosted, no guard)
Policy `escalate` (Muse Spark 1.3 first pass, Muse Wonder on
low confidence, threshold 0.75), shipped prompt.
Overall 80.2%; decisive 82.3% (n=62); high-confidence 79.5% (n=78);
per-class Confirm 81.1%, Act 75.9%, Ignore 82.9%; 2 Confirm-to-Act
errors; 15 escalations; total cost $1.67.
Verified Oct 29, 2026: a key-less replay from this run's response cache
reproduced results.jsonl byte for byte, so the committed artifacts are
provably the shipped prompt's output.

## final-107-guard: the shipped engine (hosted lane, guard on)
Same policy and shipped prompt as final-107-live, with the Phase 0
deterministic stay-change/money guard active (src/jev/change_guard.py
wraps the triage decision: stay-change and money messages come out
Confirm at any model confidence). Overall 77.2%; decisive 82.5% (n=57);
high-confidence 78.8% (n=80); per-class Confirm 83.8%, Act 55.2%,
Ignore 88.6%; zero Confirm-to-Act errors (final-107-live had 2);
16 escalations; zero fail-safes; total cost $1.85.
The guard deliberately trades headline accuracy for safety. The eval
labels predate the operator's Confirm policy for stay changes and
money, so guarded rows that flip Act to Confirm score as wrong against
the old labels while matching the operator's actual policy. On the 28
live messages graded as of Oct 29 (operator-labeled or rule-labeled),
agreement rises from 22/28 (unguarded) to 25/28 (guarded), and all
three stay-change misses are corrected.

## final-107-fixed3: reverted prompt revision (kept for honesty)
Same policy with a stricter prompt that tried to route every money or
stay-change message to Confirm. Overall 77.2%; decisive 82.5% (n=57);
high-confidence 75.6% (n=86). Zero Confirm-to-Act errors, but Act recall
fell to 58.6% and it introduced a false Act of its own. Measured,
compared row by row against final-107-live, and reverted: the
shipped prompt is the better one, and the money guarantee lives in the
autonomy code instead (the charge_guest hard lock).

## final-107-open3: open-weights run under the reverted prompt
Policy `open_escalate` (Muse Spark Open on Baseten first pass, Muse
Wonder escalation, same threshold). Overall 74.3%; decisive 83.0%
(n=53); high-confidence 76.2% (n=84); zero false Acts; cost $1.16.
Kept as the record of the open-weights tier under that prompt; the
shipped-prompt open run is final-107-open-a.

## final-107-open-a: open-weights run under the shipped prompt, no guard
Policy `open_escalate`, shipped prompt, run Oct 29, 2026. Overall
81.2%; decisive 79.4% (n=68); high-confidence 81.7% (n=82); per-class
Confirm 75.7%, Act 86.2%, Ignore 82.9%; 5 false Acts (3 Confirm-to-Act);
13 escalations; zero fail-safes; cost $1.42. Within run-to-run noise
of the hosted run overall; higher Act recall in that run, at lower
cost; the trade is more over-acting, which approve-first absorbs. It
ran before the guard existed, so it is the open model's raw record; the
guarded open lane is ablation-2026-10-31-open.

## ablation-2026-10-31-open: the shipped engine, open-weights lane
Policy `open_escalate` (Muse Spark Open on Baseten first pass, Muse
Wonder escalation, threshold 0.75), shipped prompt and engine with the
guard on, run Oct 31, 2026 from a new, empty response cache. Overall
75.2% with 1 Confirm-to-Act error. This is the open lane the sandbox
dashboard shows next to final-107-guard; full figures are in its
stats.json and scorecard.md.

## ablation-2026-10-31-spark: hosted Muse Spark alone, the same night
Policy `spark_only` (Muse Spark 1.3, no escalation), shipped prompt and
engine with the guard on, run right after ablation-2026-10-31-open from
its own empty cache. Overall 76.2% with 0 Confirm-to-Act errors. Against
ablation-2026-10-31-open the difference is not significant (exact
McNemar p = 1.0): on fresh runs, dual routing did not measurably beat
Spark alone, so escalation is kept for the low-confidence re-decide,
not claimed as an accuracy gain. Treat 1 to 2 point differences between
runs as noise.

Note on ledgers: ledger.json files were regenerated Oct 29, 2026 under
precision semantics (grouped by predicted category, which is what the
autonomy ladder actually gates on). The earlier files grouped by ground
truth. Outcomes are unchanged: every category sits at approve-first
(P1) in every run.
