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
The two live plays run end to end on the staged fixture world
(jev.plays.default_fixture_data: three properties on 2026-10-28, one
per early check-in outcome, plus the cleaner-done records). Play runs
are kept in memory server-side; the sandbox engine's autonomy ledger
is empty, so every action right is P1 (approve_first) — steps produce
their drafts and are HELD at the gate, which is the point of the
demo. Nothing is really sent or charged; any step that ever executes
does so through the simulated channel and is labeled simulated.

Endpoints:
  POST /play   start a run. Body for early check-in:
               {"play": "early_checkin", "message": "...",
                "property_id": "cedar-hollow-2n", "date": "2026-10-28"}
               Body for cleaner-done (the event is the trigger):
               {"play": "cleaner_done", "property_id": "cedar-hollow-2n",
                "date": "2026-10-28", "cleaner_id": "cleaner-cristina",
                "finished_at": "1:32 PM"}
  POST /event  deliver an event to a waiting run:
               {"run_id": "...", "event": "cleaner_done",
                "payload": {"finished_at": "1:32 PM"}}
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
