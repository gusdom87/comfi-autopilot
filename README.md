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
- Special requests: late checkout is built, checked against the next arrival and the turnover in between; when the ask runs into the next guest, the drafted reply offers the latest time that works, and any fee is its own held step. The other request types (supplies, a crib, parking) are designed on the same rails.
- Exterior-camera oversight: the camera plays work from the frames and camera status that a camera connection supplies, and send anything unexpected outside the home to the operator. This build uses staged frames and staged camera status; it has no live camera connection. See Camera support and limits below.

## How Jev fits

Jev is not this product. The urgency call on each message is made by the triage layer in this repo, a hackathon-built port of the operator's Jev pipeline (TypeSafe AI) to Muse Spark/Wonder; Jev's original tool, the hand-labeled dataset, and the Oct 7–14 pilot are pre-existing. The standard the triage layer is measured against is the operator's own judgment, recorded by hand: a pilot set of roughly 300 real guest messages he labeled himself, from which the graded eval set is drawn. Nothing here is trained; the models are prompted, and the graded record is what they answer to. Comfi Autopilot is everything around that decision: the connection to the booking data, the plays that turn a decision into coordinated work, and the autonomy ladder that decides what the system may do with it.

## Graduated autonomy

Send rights are earned from the graded record, never assumed: at P1 every action needs a human's approval, at P2 a category that has proven itself may send with a log, and at P3 it sends with spot checks. Fail-safe everywhere: on any error, timeout, or unparsable output, the decision falls back to Confirm and a human decides.

The graded record so far, from the eval harness in this repo (full 107-message eval set, 6 few-shot examples excluded from scoring, n=101 scored; configuration of record: Spark-to-Wonder escalation with the shipped prompt): overall accuracy 80.2%, decisive accuracy 82.3% (n=62), high-confidence accuracy 79.5% (n=78), per-class accuracy Confirm 81.1%, Act 75.9%, Ignore 82.9%, 2 Confirm-to-Act errors, zero Ignore-to-Act errors, and 15 escalations, for a total cost of $1.67. Autonomy rights probed from this run's ledger keep Confirm, Act and Ignore all at approve-first: no category has yet met the ladder's P2 thresholds, so Comfi Autopilot correctly runs at P1, approve-first, today.

## Disclosure: pre-existing vs hackathon-built

Pre-existing, disclosed as background and baseline only: Jev, the decision model by TypeSafe AI; the operator's hand-labeled dataset of real guest messages; and the shadow pilot the operator ran Oct 7–14, 2026, which graded the triage decisions the eval set is built from. None of that is presented as hackathon work.

Built during the hackathon, Oct 26 to Nov 4, 2026 — the Comfi Autopilot layer in this repo: the port of the pipeline to the Meta Model API (Muse Spark for fast triage, Muse Wonder for reasoning and drafts), the eval harness and confidence ledger, the graduated-autonomy ladder, the plays engine (early check-in, cleaner-done to guest-ready, in-stay issues with handyman dispatch, exterior-camera checks, late checkout), the outcome metrics, the deterministic frame gate for camera stills (plain code that refuses any frame without a usable, unclipped subject before a model or a human ever sees it as evidence), and this judge sandbox.

## Camera support and limits

This build has no live camera connector and no live camera feed. Every camera play in the sandbox runs on staged frames and staged camera status. The live vision path was verified against the Meta Model API on Oct 27, 2026 with a test image; that is a vision check, not a camera integration.

In production, a camera connection has to supply motion-triggered frames, a fresh frame on demand, repeated frames at separate times, the latest frames on request, online and offline events with retained history, a stable mapping of each camera to one property, and frames clear enough for the frame gate and the vision check.

Production camera support is limited to these classes. None of these connectors is built in this repo.

- Certified ONVIF Profile S/T or RTSP IP camera systems, including tested NVR channels.
- UniFi Protect cameras through a UniFi OS Protect console.
- Selected powered Wyze models through docker-wyze-bridge, which is community software and not a Wyze-published integration.

Support is certified by exact model, firmware, power type, and connector version. A brand name alone is never a support promise. Battery-powered cameras that sleep and app-only cameras are not supported for the full camera feature set. Blink, Ring, Arlo, and Google Nest (support varies by model) are not full-support promises. The full boundary, the named models and exclusions, and the certification checks are in [docs/CAMERA-SUPPORT.md](docs/CAMERA-SUPPORT.md).

## Run it

Judge sandbox (staged fixture world, no real guest data; the API key stays server-side):

    JEV_SANDBOX_PORT=8791 python3 sandbox/server.py

Then open http://127.0.0.1:8791. See sandbox/README.md for the endpoints and a click-by-click script for each scenario. With no API key in the server environment, triage returns the Confirm fail-safe by design.

Eval harness (the dataset itself is private and not in this repo; pass a path to a redacted dataset):

    python3 eval/run_eval.py --dataset <path> --policy escalate --examples 6 --out <dir> --budget-usd <cap>

Add --mock to run with no live model calls. See eval/README.md and docs/DATASET.md for how the ground truth was built.
