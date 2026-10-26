# Architecture (build notes, Oct 26, 2026)

Four workstreams:
1. Core port: triage pipeline on the Meta Model API (Spark = fast triage,
   Wonder = reasoning and drafts), live on one owned Cedar Hollow property,
   fail-safe default = Confirm on any failure.
2. Eval harness + confidence ledger: ablation across tiering policies,
   scored against operator-labeled ground truth.
3. Outcome metrics from Hospitable timestamps (response time, no-touch rate,
   dispatch latency).
4. Judge sandbox page: paste a message, get class, confidence, draft, route,
   reason.

Graduated autonomy: per-category send rights are EARNED from the graded
record, not assumed. Ladder: P1 everything needs approval; P2 low-risk
categories send after a clean streak; P3 broader rights with spot checks.
The ledger is the evidence; the demo shows the ladder, not a claim.
