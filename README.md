# Comfi Autopilot

One-line pitch: an autonomous operations system for short-term rentals that runs the day-to-day of a hosting business, and only acts on its own as far as its graded record has earned.

Built Oct 26 to Nov 4, 2026 for the Meta Global AI Developer Hackathon (Indie track).

## What it is

Comfi Autopilot is designed to connect to Hospitable, the property-management system the bookings already live in, and to work from the real operating data: every reservation, check-in time, and cleaning on the calendar. This build runs on a staged fixture world in that data model (no live Hospitable or Turno connection). On top of that data it coordinates the work of the business end to end — the guest asking, the cleaner finishing, the issue mid-stay — as one operation instead of a pile of separate messages. Every action it might take is gated by graduated autonomy: closing noise is a low right, messaging a guest or a cleaner is a middle right, and charging a guest is the highest right, human-approved until earned.

What it operates:

- Guest communication, around the clock: every incoming message is read and sorted by urgency, and the urgent ones (Act) get a drafted reply in the operator's voice.
- Cleaning turnovers: cleanings lined up between one guest leaving and the next arriving, with the arriving guest told the moment their place is ready.
- Early check-in: a guest's request is checked against the same-day turnover reality; when it works, a yes reply is drafted and held for approval along with the fee step.
- In-stay issues: problems during a stay, from a door that will not unlock to getting a handyman dispatched.
- Special requests: handled against the same calendar and turnover reality as everything else.
- Exterior-camera oversight: the outside of the home is watched, so someone is looking out for the property even when the operator is not there.

## How Jev fits

Jev is not this product. The urgency call on each message is made by the triage layer in this repo, a hackathon-built port of the operator's Jev pipeline (TypeSafe AI) to Muse Spark/Wonder; Jev's original tool, the hand-labeled dataset, and the Oct 7–14 pilot are pre-existing. The operator taught the triage layer his operation's judgment by hand-labeling roughly 300 real guest messages himself. Comfi Autopilot is everything around that decision: the connection to the booking data, the plays that turn a decision into coordinated work, and the autonomy ladder that decides what the system may do with it.

## Graduated autonomy

Send rights are earned from the graded record, never assumed: at P1 every action needs a human's approval, at P2 a category that has proven itself may send with a log, and at P3 it sends with spot checks. Fail-safe everywhere: on any error, timeout, or unparsable output, the decision falls back to Confirm and a human decides.

The graded record so far, from the eval harness in this repo (89-message eval set, 6 few-shot examples excluded from scoring, n=83 scored; configuration of record: Spark-to-Wonder escalation with the tuned2 prompt): overall accuracy 84.3%, decisive accuracy 81.8%, high-confidence accuracy 88.5%, Act recall 75.0%, Ignore accuracy 100%, and zero Confirm-to-Act errors. No category has yet met the ladder's P2 thresholds, so Comfi Autopilot correctly runs at P1, approve-first, today.

## Disclosure: pre-existing vs hackathon-built

Pre-existing, disclosed as background and baseline only: Jev, the decision model by TypeSafe AI; the operator's hand-labeled dataset of real guest messages; and the shadow pilot the operator ran Oct 7–14, 2026, which graded the triage decisions the eval set is built from. None of that is presented as hackathon work.

Built during the hackathon, Oct 26 to Nov 4, 2026 — the Comfi Autopilot layer in this repo: the port of the pipeline to the Meta Model API (Muse Spark for fast triage, Muse Wonder for reasoning and drafts), the eval harness and confidence ledger, the graduated-autonomy ladder, the plays engine (early check-in, cleaner-done to guest-ready), the outcome metrics, the deterministic frame gate for camera stills (plain code that refuses any frame without a usable, unclipped subject before a model or a human ever sees it as evidence), and this judge sandbox.

## Run it

Judge sandbox (staged fixture world, no real guest data; the API key stays server-side):

    JEV_SANDBOX_PORT=8791 python3 sandbox/server.py

Then open http://127.0.0.1:8791. See sandbox/README.md for the endpoints and a click-by-click script for each scenario. With no API key in the server environment, triage returns the Confirm fail-safe by design.

Eval harness (the dataset itself is private and not in this repo; pass a path to a redacted dataset):

    python3 eval/run_eval.py --dataset <path> --policy escalate --examples 6 --out <dir> --budget-usd <cap>

Add --mock to run with no live model calls. See eval/README.md and docs/DATASET.md for how the ground truth was built.
