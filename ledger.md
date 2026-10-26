# Decision Ledger

A before/during record for the Meta Global AI Developer Hackathon build (Oct 26 to Nov 4, 2026).

## Rules of this ledger

- Every material decision gets a dated entry: what was decided, why, and what it rules out.
- "Before" entries describe pre-event background (pilot, labeled data, operator experience). They are never presented as hackathon-built work.
- "During" entries describe work built inside the event window only. The demo highlights during-entries only.

## Entries

- 2026-10-26 (during): Fresh public repo created at go-live. No pre-existing Jev code in this repo.
- 2026-10-26 (during): Day 0 scaffold committed and pushed: package layout, fail-safe Meta API client (any failure returns no-decision; callers fall back to Confirm), tiering policy entry points, eval/sandbox/metrics skeletons, architecture notes. Fail-safe tests pass (2).
- 2026-10-26 (during): Meta Model API facts pinned in docs/API-NOTES.md: base https://api.meta.ai/v1, POST /responses, model IDs muse-spark-1.3 and muse-wonder-1.0 with published prices. API key creation is gated on a payment method being on file, so the first live call waits on both the $150 credits posting and the card decision.
- 2026-10-26 (before, disclosed): PII redaction pass completed on the staged pilot dataset copies (109 verdicts, 107 graded decisions) in Milo's private workspace: names, addresses, and identifiers replaced or removed, verification scan clean. The redacted set is the eval dataset; the mapping stays private and the dataset is not published.
- 2026-10-26 (during): Four build specs written for Claude Code: core port, eval harness, outcome metrics, sandbox page. Build order follows the Fable plan.
- 2026-10-26 (during): Core port built and tested (spec 01): triage/draft prompts, strict JSON parsing with fail-safe, three tiering policies (Spark only, Wonder only, escalate at 0.75), graduated-autonomy ladder reading the confidence ledger, daily spend guard, one-message CLI. 7 tests passing, all model calls mocked. Builder note: the Mac mini's terminal is not reachable through Milo's device controls (terminal apps are excluded from Mac control), so Milo is building directly in this repo; the four specs remain the build contract.
