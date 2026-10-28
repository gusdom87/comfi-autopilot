# Comfi Autopilot — judge sandbox
The demo page for Comfi Autopilot, an autonomous operations system for
short-term rentals connected to Hospitable (guest communication, cleaning
turnovers, in-stay issues, handyman dispatch, special requests, and
exterior-camera oversight). The urgency decisions inside it are made by
Jev, an existing decision tool by TypeSafe AI that the operator runs as a
component of the system; Comfi Autopilot is the product on this page.

One command, no build step:
  JEV_SANDBOX_PORT=8791 python3 sandbox/server.py
Then open http://127.0.0.1:8791. The API key comes from the server
environment (see .env.example); it never reaches the browser. Example
messages are staged for the demo; no real guest data is used anywhere.

## Triage (top of the page)
Paste a guest message (or use a staged chip) and press Triage:
POST /triage runs the escalate policy server-side and returns the
category, confidence, draft, route, reason, and the autonomy right
that category has earned. With no API key in the server environment,
every message returns the Confirm fail-safe, by design.

## Plays (bottom of the page)
The live plays run end to end on the staged fixture world
(jev.plays.default_fixture_data: three properties on 2026-10-28, one
per early check-in outcome, plus the cleaner-done records and one
mid-stay record for the camera play). Play runs are kept in memory
server-side; the sandbox engine's autonomy ledger is empty, so every
action right is P1 (approve_first) — steps produce their drafts and
are HELD at the gate, which is the point of the demo. Nothing is
really sent or charged; any step that ever executes does so through
the simulated channel and is labeled simulated.

Endpoints:
  POST /play   start a run. Body for early check-in:
               {"play": "early_checkin", "message": "...",
                "property_id": "lockport-2n", "date": "2026-10-28"}
               Body for cleaner-done (the event is the trigger):
               {"play": "cleaner_done", "property_id": "lockport-2n",
                "date": "2026-10-28", "cleaner_id": "cleaner-cristina",
                "finished_at": "1:32 PM"}
               Body for camera check (the camera event is the trigger):
               {"play": "camera_check", "property_id": "lockport-1n",
                "date": "2026-10-29", "camera": "backyard",
                "ts": "2026-10-29T21:48:00", "frame_id": "f3"}
               Body for in-stay issue (a guest message, like early
               check-in; the play extracts the issue itself):
               {"play": "in_stay_issue", "message": "...",
                "property_id": "lockport-1n", "date": "2026-10-29"}
  POST /event  deliver an event to a waiting run:
               {"run_id": "...", "event": "cleaner_done",
                "payload": {"finished_at": "1:32 PM"}}
               The in-stay issue play waits for vendor events:
               {"run_id": "...", "event": "vendor_eta",
                "payload": {"eta": "3:30 PM"}} and then
               {"run_id": "...", "event": "vendor_done",
                "payload": {"finished_at": "4:05 PM"}}
  GET  /run/<id>   the run's current state (same JSON /play returns).

Driving it on the page:
- Early check-in: pick Studio 2N (a 1 PM ask against a 2:00 PM ready
  time goes conditional), press "Run the early check-in play", and
  read the timeline top to bottom: requested time extracted, reality
  checked, guest reply drafted, the $25 charge HELD at the
  highest-stakes gate, the cleaner ping HELD, and the run parked on
  the wait step. Then press "Cleaner reports done": the run wakes,
  and the guest-ready update completes the chain. Owner's Unit 1N
  (no turnover) shows a grant; Peter's House shows a deny.
- Cleaner done: "Cleaner finished — Studio 2N" runs Play 3 alone:
  the clean is verified against the record and the guest-ready send
  is held at the message_guest gate. "Cleaner finished — Peter's
  House" uses the fixture whose cleaner flagged a problem (a cracked
  wine glass): the run ends held_for_human with the flag summarized,
  and the guest-ready step never runs — nothing is sent.
- Camera check: "Camera motion — guest arriving, Studio 2N" runs
  Play 7 with staged frame f1: the frame classifies as the expected
  guest (Dana arrives that day), the run logs it through the
  auto_close gate, and nobody is notified. "Camera motion — large
  gathering, Owner's Unit 1N" uses frame f3: eleven people on the
  patio against a booked party of four mid-stay, so the run flags
  the OPERATOR — the notification is held at the notify_operator
  gate with its summary preserved. This play never messages a
  guest. Frames are staged metadata (jev.plays.vision.STAGED_FRAMES),
  classified by the fixture vision implementation; the live vision
  path was verified against the real API on 2026-10-27 (see
  vision.py for the confirmed request shape) but the sandbox never
  uses it.
- In-stay issue: "Guest reports a leak — Owner's Unit 1N" runs
  Play 4 against the mid-stay fixture (Alex, in residence
  2026-10-29): the issue is extracted, the stay and the fixture
  handyman are verified, the guest reply is drafted with a safe
  self-help step, and the dispatch is HELD at the dispatch_vendor
  gate with the property's staged access notes in the artifact.
  Press "Vendor update: arriving 3:30 PM" and the guest ETA update
  is drafted and held; press "Vendor reports done" and the
  fix-done guest update completes the chain. "Guest smells gas"
  runs the safety branch: the guest reply carries safety guidance
  instead of self-help, and the operator notification is produced
  and held in the same run. Ending the chain with the done event
  alone (no ETA first) also works — the ETA steps skip.
