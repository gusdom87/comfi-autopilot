# Brief: judge-facing LIVE OPERATION console (2026-10-31)

Repo: this repo, branch design/relevo-skin. Work in `sandbox/`
(`index.html` single-file page + `server.py` stdlib server). EXTEND the
existing page; do not rewrite it. Everything on the current page must
survive: POST /triage round trip and all result fields, property picker,
every play (POST /play, POST /event, GET /run), camera scenarios, gate /
draft / simulated labels exactly as worded, disclosure texts verbatim.
Visual language: design/spec.md + design/spec-v2.md + typography-final.md
(Libre Franklin, self-hosted embedded faces already in the page; palette
ink #101E3C, ground #F7F3EB, panel #FFFDF8, amber #C7924B, tan #D9B07A,
success #2F7D5B, danger #B24A3A; pressed-card and inset-well shadows;
badges: ACT navy/cream, CONFIRM amber/navy, IGNORE tan/navy).

## Goal

A new top section, id="live", heading "Live operation", placed directly
under the masthead and before the Triage section, plus a "Live" anchor in
the rail. A judge opening the page sees within seconds a live autonomous
operation being graded. All data is REAL: the live shadow ledger and the
two measured eval runs. Nothing fabricated: no invented guest names
(the ledger's guest field is blank by design — never render a name),
no invented statistics. Every number must be computed from the sources.

## Data sources (read-only)

1. Shadow ledger JSONL: path from env JEV_SHADOW_LEDGER, default
   `/home/hatch/workspace/hackathon/shadow-live/ledger.jsonl`.
   Row fields include: decided_at, message_at, property, listing,
   platform, reservation_code, message_text, engine_category
   (Act/Confirm/Ignore), confidence, tier (e.g. "muse-spark-1.3" or
   "muse-spark-1.3 -> muse-wonder-1.0" when escalated), route, reason,
   draft, cost_usd, answered_by_human, host_outcome (host_replied /
   no_reply_24h / ""), host_reply_latency_min, verdict, verdict_basis,
   gus_category (operator label, may be absent), gus_draft_verdict,
   gus_note, never_graduate, never_graduate_topics.
2. Eval runs in this repo:
   - `eval/runs/final-107-fixed3/stats.json` (+ results.jsonl tier_path)
     — policy "escalate": Muse Spark via Meta Model API first pass,
     Muse Wonder escalation.
   - `eval/runs/final-107-open3/stats.json` (+ results.jsonl)
     — policy "open_escalate": Muse Spark Open (open weights) via
     Baseten first pass, Muse Wonder escalation.
   Escalation threshold is 0.75 (src/jev/tiers.py ESCALATION_THRESHOLD).
   Escalation count for a run = results.jsonl rows whose tier_path
   contains "->".

## Server work (sandbox/server.py, stdlib only)

Add `GET /shadow-data` returning JSON computed fresh per request:
- `rows`: the ledger rows, in decided_at order, trimmed to the fields
  the page needs (all fields above are fine to pass through).
- `totals`: row count; counts by engine_category; labeled count
  (gus_category present); labeled agreement count and pct
  (gus_category == engine_category); escalations (tier contains "->");
  total cost_usd; never_graduate flag count.
- `windows`: over labeled rows in decided order: window 1 = first 20
  labeled rows (agreement count/pct), window 2 = the rest (n so far,
  agreement count/pct). If fewer than 20 labeled rows, window 1 is
  partial — report n honestly.
- `by_category`: for each of Act/Confirm/Ignore: engine-called count
  (labeled rows only), operator-agreed count.
- `evals`: for each run: name, policy label, stats.json contents
  (accuracy, decisive_accuracy, decisive_n, n, total_cost_usd,
  high_conf_accuracy, high_conf_n, by_category, confusion), escalations
  count, and `confirm_to_act` = confusion.Confirm.Act.
- `zero_dangerous`: sum of confirm_to_act across both eval runs.
- Tolerate a missing/unreadable ledger or stats file: return what exists
  with an `errors` list; never 500.

## Page work (sandbox/index.html, vanilla JS, no build step)

Section "Live operation" with four panels:

1. SCOREBOARD strip (pressed stat cards):
   - Decisions in the live shadow ledger: N (live count from /shadow-data)
   - Shadow agreement (operator-labeled): A/L (P%)
   - Verdict counts: Act n · Confirm n · Ignore n
   - Escalations to Muse Wonder: n
   - Model spend, live shadow: $X
   - ZERO card, visually dominant (navy panel, cream text, amber zero):
     big "0" — label exactly: "Dangerous false actions — truth Confirm,
     predicted Act — across both measured eval runs (202 decisions)".
     The number comes from zero_dangerous in the payload.
2. LIVE FEED + SHADOW GRADING (the centerpiece):
   - A visible honesty label: "Replay of the live shadow ledger. Real
     decisions on real guest messages. Nothing was sent — the engine is
     graded against what the host actually did."
   - Replay rows oldest→newest, one new card every ~2.2s, looping back
     to the start when exhausted. Poll /shadow-data every 30s; if the
     ledger grew, append the genuinely new rows to the queue.
   - Card content, phase 1 (decision lands): property, platform,
     reservation code, message time, message_text (truncate ~300 chars),
     category badge (ACT/CONFIRM/IGNORE per spec badge semantics),
     confidence %, tier path (show "escalated to Muse Wonder" when the
     tier contains "->"), reason line, and for Act/Confirm rows the draft
     in an inset well.
   - Phase 2 (~0.9s later, grading reveal): if gus_category present:
     "Operator: <gus_category>" + AGREE (green) or DISAGREE chip;
     if absent: "Operator label pending". Host outcome line:
     host_replied → "Host replied in X min"; no_reply_24h → "No host
     reply in 24h"; answered_by_human true → "Answered by the host".
     Show gus_note (truncated) when present on labeled rows — it is the
     operator's own reasoning and it is gold for judges.
   - Running tally in the panel head updates as the replay progresses
     (shown / agreed / disagreed counts for revealed cards).
3. EVAL PANEL — "Two measured runs":
   - Two run cards side by side from the evals payload: run name,
     policy, overall accuracy %, decisive accuracy % (n), high-conf
     accuracy %, records scored, total cost, escalations, per-category
     accuracy (Confirm/Act/Ignore), and the full confusion matrix as a
     small table (rows = truth, columns = prediction) — the Confirm row
     is the ZERO's source; let judges see the 0 cell.
   - Routing diagram (pure HTML/CSS boxes and arrows, no images):
     Lane A (fixed3): Guest message → Muse Spark (Meta Model API) →
     confidence ≥ 0.75 accept / < 0.75 → Muse Wonder (escalation).
     Lane B (open3): Guest message → Muse Spark Open (Baseten, open
     weights) → same gate → Muse Wonder. Put each lane's escalation
     count on its arrow.
4. AUTONOMY GATES panel:
   - The rule, worded exactly: "A category earns send rights only at
     ~95% agreement across two consecutive 20-message windows. Money
     and safety topics never graduate — a human always decides those."
   - Window status from the payload: Window 1 (20 labeled): n, agreement
     %; Window 2: n of 20, agreement %; bar/verdict: "Below the 95% bar —
     still in shadow. Every category is held at P1 approve-first."
   - Per-category table (Act / Confirm / Ignore): engine calls graded,
     operator agreed, agreement %.
   - Never-graduate line: "Money / safety flags in the live ledger so
     far: N" (payload count; currently 0 — render the real number).

## Constraints

- Keep the single-file page + stdlib server architecture. No new
  dependencies, no build step, no external assets.
- Do not alter existing sections' wording, ids, or wiring. Add new CSS
  prefixed `lv-` to avoid collisions.
- The page must still work if /shadow-data fails (panels show a quiet
  "ledger unavailable" note; the rest of the page is unaffected).
- Tests: add coverage for the new server aggregation (a test file under
  tests/, e.g. test_sandbox_live.py) with a tiny fixture ledger + stats,
  asserting totals, windows, and confirm_to_act math. The FULL existing
  test suite must pass when you are done. Run it and report the result.
- Commit on this branch when green, with a clear message. Do not merge,
  do not deploy.
