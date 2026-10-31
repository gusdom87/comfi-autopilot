# Comfi Autopilot

One-line pitch: an autonomous operations system for short-term rentals that runs the day-to-day of a hosting business, and only acts on its own as far as its graded record has earned.

Built Oct 26 to Nov 4, 2026 for the Meta Global AI Developer Hackathon (Indie track, theme: Build for Your World).

## What it is

Comfi Autopilot is designed to connect to Hospitable, the property-management system the bookings already live in, and to work from the real operating data: every reservation, check-in time, and cleaning on the calendar. This build runs on a staged fixture world in that data model (no live Hospitable or Turno connection). On top of that data it coordinates the work of the business end to end — the guest asking, the cleaner finishing, the issue mid-stay — as one operation instead of a pile of separate messages. Every action it might take is gated by graduated autonomy: closing noise is a low right, messaging a guest or a cleaner is a middle right, and charging a guest is the highest right, human-approved until earned.

What it operates:

- Guest communication, around the clock: every incoming message is read and sorted by urgency, and the urgent ones (Act) get a drafted reply in the operator's voice.
- Cleaning turnovers: cleanings lined up between one guest leaving and the next arriving, with the arriving guest told the moment their place is ready.
- Early check-in: a guest's request is checked against the same-day turnover reality; when it works, a yes reply is drafted and held for approval along with the fee step.
- In-stay issues: problems during a stay, from a door that will not unlock to getting a handyman dispatched.
- Special requests: late checkout is built, checked against the next arrival and the turnover in between; when the ask runs into the next guest, the drafted reply offers the latest time that works, and any fee is its own held step. The other request types (supplies, a crib, parking) are designed on the same rails.
- Exterior-camera oversight: the camera plays work from the frames and camera status that a camera connection supplies, and send anything unexpected outside the home to the operator. This build uses staged frames and staged camera status; it has no live camera connection. See Camera support and limits below.

## How Jev fits

Jev is not this product. The urgency call on each message is made by the triage layer in this repo, a hackathon-built port of the operator's Jev pipeline (TypeSafe AI) to Muse Spark/Wonder; Jev's original tool, the hand-labeled dataset, and the Oct 7–14 pilot are pre-existing. The standard the triage layer is measured against is the operator's own judgment, recorded by hand: a pilot set of roughly 300 real guest messages he labeled himself, from which the graded eval set is drawn. Nothing here is trained; the models are prompted, and the graded record is what they answer to. Comfi Autopilot is everything around that decision: the connection to the booking data, the plays that turn a decision into coordinated work, and the autonomy ladder that decides what the system may do with it.

## Graduated autonomy

Send rights are earned from the graded record, never assumed: at P1 every action needs a human's approval, at P2 a category that has proven itself may send with a log, and at P3 it sends with spot checks. P2 needs at least 25 decisive calls with decisive accuracy of 95% or better and high-confidence accuracy of 98% or better; P3 needs 75, 97%, and 98% (src/jev/autonomy.py). These thresholds are fixed design constants, and the measured record is held against them. Charging a guest is never on the ladder. Fail-safe everywhere: on any error, timeout, or unparsable output, the decision falls back to Confirm and a human decides.

**The shipped engine runs guarded.** Every triage verdict passes through a deterministic stay-change and money guard (plain code in src/jev/change_guard.py; `tiers.decide` always applies it), so a stay-change or money message comes out Confirm at any model confidence. Its graded record, from the eval harness in this repo (full 107-message eval set, 6 few-shot examples excluded from scoring, n=101 scored; Spark-to-Wonder escalation with the shipped prompt; run final-107-guard): overall accuracy 77.2%, decisive accuracy 82.5% (n=57), high-confidence accuracy 78.8% (n=80), per-class accuracy Confirm 83.8%, Act 55.2%, Ignore 88.6%, zero Confirm-to-Act errors, and 16 escalations, for a total cost of $1.85 with zero fail-safes. The guard deliberately trades headline accuracy for safety. The eval labels predate the operator's Confirm policy for stay changes and money, so a guarded row that flips Act to Confirm scores as wrong against the old label while matching the operator's actual policy. No category meets the ladder's P2 thresholds, so Comfi Autopilot correctly runs at P1, approve-first, today.

**The model's raw record without the guard** (run final-107-live: the same eval set, prompt, and policy, with the model's own verdicts): overall accuracy 80.2%, decisive accuracy 82.3% (n=62), high-confidence accuracy 79.5% (n=78), per-class accuracy Confirm 81.1%, Act 75.9%, Ignore 82.9%, 2 Confirm-to-Act errors, zero Ignore-to-Act errors, and 15 escalations, for a total cost of $1.67. It is kept as the record of what the model decides on its own, not as the shipped behavior; its ledger also keeps Confirm, Act, and Ignore at approve-first.

The open-weights lane (Muse Spark Open on Baseten as the first pass, Muse Wonder on low confidence, the same prompt, threshold, and guard) was run fresh on Oct 31 (run ablation-2026-10-31-open): 75.2% overall with 1 Confirm-to-Act error. A hosted Muse Spark run with no escalation, the same night, scored 76.2% with 0 (run ablation-2026-10-31-spark). The difference is not significant (exact McNemar p = 1.0), so escalation is kept for the low-confidence re-decide, not claimed as an accuracy gain. The sandbox dashboard's Confirm-to-Act tile counts the shipped engine's two runs, one per lane: final-107-guard 0 and ablation-2026-10-31-open 1.

On the live record, 28 live messages graded as of Oct 29 (operator-labeled or rule-labeled), agreement rises from 22/28 without the guard to 25/28 with it, and all three stay-change misses are corrected. The live shadow that produced them runs triage on real inbound guest messages and never sends; its record is shown on the dashboard as a live record, not claimed as an eval result.

## Disclosure: pre-existing vs hackathon-built

Pre-existing, disclosed as background and baseline only: Jev, the decision model by TypeSafe AI; the operator's hand-labeled dataset of real guest messages; and the shadow pilot the operator ran Oct 7–14, 2026, which graded the triage decisions the eval set is built from. None of that is presented as hackathon work.

Built during the hackathon, Oct 26 to Nov 4, 2026 — the Comfi Autopilot layer in this repo: the port of the pipeline to the Meta Model API (Muse Spark for fast triage, Muse Wonder for reasoning and drafts), the eval harness and confidence ledger, the graduated-autonomy ladder, the plays engine (early check-in, cleaner-done to guest-ready, in-stay issues with handyman dispatch, exterior-camera checks, late checkout), the outcome metrics, the deterministic frame gate for camera stills (plain code that refuses any frame without a usable, unclipped subject before a model or a human ever sees it as evidence), and this judge sandbox.

Also built: a per-property knowledge base module (src/jev/knowledge.py), with trust-tagged facts in three statuses (known, unverified, unknown), a code scrubber tested on dial-format codes, and an operator-confirmation promotion log. It ships with 12 tests and is not yet wired into the plays.

## Camera support and limits

This build has no live camera connector and no live camera feed. Every camera play in the sandbox runs on staged frames and staged camera status. The live vision path was verified against the Meta Model API on Oct 27, 2026 with a test image; that is a vision check, not a camera integration.

In production, a camera connection has to supply motion-triggered frames, a fresh frame on demand, repeated frames at separate times, the latest frames on request, online and offline events with retained history, a stable mapping of each camera to one property, and frames clear enough for the frame gate and the vision check.

Production camera support is limited to these classes. None of these connectors is built in this repo.

- Certified ONVIF Profile S/T or RTSP IP camera systems, including tested NVR channels.
- UniFi Protect cameras through a UniFi OS Protect console.
- Selected powered Wyze models through docker-wyze-bridge, which is community software and not a Wyze-published integration.

Support is certified by exact model, firmware, power type, and connector version. A brand name alone is never a support promise. Battery-powered cameras that sleep and app-only cameras are not supported for the full camera feature set. Blink, Ring, Arlo, and Google Nest (support varies by model) are not full-support promises. The full boundary, the named models and exclusions, and the certification checks are in [docs/CAMERA-SUPPORT.md](docs/CAMERA-SUPPORT.md).

## Run it

Requirements: Python 3.10 or newer. The code uses the standard library only; the tests need pytest.

Tests (no key and no network needed; 437 tests at this commit):

    python3 -m pytest -q

API key: the sandbox and the eval harness read their settings from the process environment, and nothing loads `.env` for you. Copy `.env.example` to `.env`, set `META_MODEL_API_KEY`, and export the file into the shell that starts the server:

    cp .env.example .env    # then fill in META_MODEL_API_KEY
    set -a; . ./.env; set +a

The open-weights lane in the eval harness (`--policy open_escalate`) also reads `BASETEN_API_KEY`. The key stays server-side and never reaches the browser.

Judge sandbox (plays and triage run on a staged fixture world with no real guest data):

    JEV_SANDBOX_PORT=8791 python3 sandbox/server.py

Then open http://127.0.0.1:8791. See sandbox/README.md for the endpoints and a click-by-click script for each scenario. With no API key in the server environment, triage returns the Confirm fail-safe by design: every message comes back Confirm with the reason "Fail-safe: model unavailable or output unparsable; a human decides."

The Live operation section at the top of the page reads the operator's private shadow ledger (`JEV_SHADOW_LEDGER`, or a default path on the operator's machine). On a fresh clone that file is absent, so the live feed, scoreboard, and gates show "Ledger unavailable." The eval run cards and the Confirm-to-Act tile still render, from the run files in eval/runs/.

Eval harness (the dataset itself is private and not in this repo; pass a path to a redacted dataset):

    python3 eval/run_eval.py --dataset <path> --policy escalate --examples 6 --out <dir> --budget-usd <cap>

Add --mock to run with no live model calls. See eval/README.md and docs/DATASET.md for how the ground truth was built.
