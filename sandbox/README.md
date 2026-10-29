# Comfi Autopilot — judge sandbox
The demo page for Comfi Autopilot, an autonomous operations system for
short-term rentals (guest communication, cleaning turnovers, in-stay
issues, handyman dispatch, special requests, and exterior-camera
oversight), designed to connect to Hospitable; this build runs on a
staged fixture world (no live Hospitable or Turno connection). The
urgency decisions inside it are made by the triage layer in this repo, a
hackathon-built port of the operator's Jev pipeline (TypeSafe AI) to Muse
Spark/Wonder; Jev's original tool, the hand-labeled dataset, and the
Oct 7–14 pilot are pre-existing. Comfi Autopilot is the product on this
page.

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
server-side; the sandbox reads the graded eval ledger (the run of
record, eval/runs/final-107-live/ledger.json), and no category has
cleared the ladder, so every action still resolves to approve-first
(P1, approve_first) — steps produce their drafts and are HELD at the
gate, which is the point of the demo. Nothing is
really sent or charged; any step that ever executes does so through
the simulated channel and is labeled simulated.

Endpoints:
  POST /play   start a run. Body for early check-in:
               {"play": "early_checkin", "message": "...",
                "property_id": "cedar-2a", "date": "2026-10-28"}
               Body for cleaner-done (the event is the trigger):
               {"play": "cleaner_done", "property_id": "cedar-2a",
                "date": "2026-10-28", "cleaner_id": "cleaner-lena",
                "finished_at": "1:32 PM"}
               Body for camera check (the camera event is the trigger):
               {"play": "camera_check", "property_id": "cedar-1a",
                "date": "2026-10-29", "camera": "backyard",
                "ts": "2026-10-29T21:48:00", "frame_id": "f3"}
               Body for in-stay issue (a guest message, like early
               check-in; the play extracts the issue itself):
               {"play": "in_stay_issue", "message": "...",
                "property_id": "cedar-1a", "date": "2026-10-29"}
               Body for late checkout (a guest message; date is the
               DEPARTURE day, and the play extracts the time itself):
               {"play": "late_checkout", "message": "...",
                "property_id": "cedar-1a", "date": "2026-11-08"}
               Body for camera offline (the switch play; mention is
               the wording variant, lights_and_camera | lights_only):
               {"play": "camera_offline", "property_id": "cedar-1a",
                "date": "2026-10-29", "camera": "front-door",
                "mention": "lights_and_camera"}
               Body for the post-turnover state check:
               {"play": "camera_post_turnover",
                "property_id": "cedar-2a", "date": "2026-10-28",
                "kind": "post_turnover", "camera": "front-door",
                "ts": "2026-10-28T14:15:00", "frame_id": "f7"}
               Body for a guest lockout report (the event assembles
               the door camera's latest frame — pinned here — and
               its door state for the operator; nothing is sent to
               the guest):
               {"play": "guest_lockout", "property_id": "cedar-2a",
                "date": "2026-10-28", "camera": "front-door",
                "ts": "2026-10-28T15:52:00", "frame_id": "f13"}
               Body for a guest-unsafe report (the operator gets
               the property's latest frames, marked urgent):
               {"play": "guest_unsafe", "property_id": "cedar-2a",
                "date": "2026-10-28", "kind": "guest_unsafe"}
               Body for the sunset dark-entry check:
               {"play": "camera_sunset_check",
                "property_id": "cedar-2a", "date": "2026-10-28",
                "kind": "sunset_check", "camera": "front-door",
                "ts": "2026-10-28T18:47:00", "frame_id": "f16"}
               Body for the morning snow check (empty homes only):
               {"play": "camera_morning_check",
                "property_id": "maple-house", "date": "2026-10-29",
                "kind": "morning_check", "camera": "front-door",
                "ts": "2026-10-29T10:30:00", "frame_id": "f21"}
               Body for the camera upkeep self-check (fleet-wide;
               the run carries no single property):
               {"play": "camera_upkeep", "date": "2026-10-28",
                "kind": "camera_upkeep"}
               Body for vendor proof of service (the vendor's stated
               arrival window starts the run, which then waits for
               the window to close — see POST /event below):
               {"play": "camera_vendor_proof",
                "property_id": "maple-house", "date": "2026-10-30",
                "vendor": "Fixture Handyman",
                "window_start": "10:00", "window_end": "12:00"}
               The gathering accumulation and the move-in signs use
               the camera_check body above, one POST per frame, in
               order: the pattern lives across frames, not in one.
               Body for an offline burst (each event runs the
               ordinary camera_offline play; the correlation layer
               evaluates the burst and each formed cluster adds its
               area-outage run to the response — cluster_run is the
               first, cluster_runs lists every one):
               {"play": "camera_outage_cluster", "events": [
                 {"property_id": "cedar-1a", "date": "2026-10-29",
                  "camera": "front-door", "ts": "2026-10-29T19:20:00"},
                 {"property_id": "cedar-2a", "date": "2026-10-29",
                  "camera": "front-door", "ts": "2026-10-29T19:26:00"},
                 {"property_id": "cedar-1a", "date": "2026-10-29",
                  "camera": "backyard", "ts": "2026-10-29T19:31:00"}]}
  POST /event  deliver an event to a waiting run:
               {"run_id": "...", "event": "cleaner_done",
                "payload": {"finished_at": "1:32 PM"}}
               The in-stay issue play waits for vendor events:
               {"run_id": "...", "event": "vendor_eta",
                "payload": {"eta": "3:30 PM"}} and then
               {"run_id": "...", "event": "vendor_done",
                "payload": {"finished_at": "4:05 PM"}}
               A camera_offline run resolves if the camera returns
               before its held step ever executes:
               {"run_id": "...", "event": "camera_online",
                "payload": {"camera": "front-door"}}
               A camera_vendor_proof run finishes when the window
               closes (the payload may list the frames the camera
               system captured; with none listed, the staged frame
               store is read):
               {"run_id": "...", "event": "vendor_window_closed",
                "payload": {}}
  GET  /run/<id>   the run's current state (same JSON /play returns).

Driving it on the page:
- Early check-in: pick Studio 2A (a 1 PM ask against a 2:00 PM ready
  time goes conditional), press "Run the early check-in play", and
  read the timeline top to bottom: requested time extracted, reality
  checked, guest reply drafted, the $25 charge HELD at the
  highest-stakes gate, the cleaner ping HELD, and the run parked on
  the wait step. Then press "Cleaner reports done": the run wakes,
  and the guest-ready update completes the chain. Garden Unit 1A
  (no turnover) shows a grant; Maple Street House shows a deny.
- Cleaner done: "Cleaner finished — Studio 2A" runs Play 3 alone:
  the clean is verified against the record and the guest-ready send
  is held at the message_guest gate. "Cleaner finished — Maple Street
  House" uses the fixture whose cleaner flagged a problem (a cracked
  wine glass): the run ends held_for_human with the flag summarized,
  and the guest-ready step never runs — nothing is sent.
- Camera check: "Camera motion — guest arriving, Studio 2A" runs
  Play 7 with staged frame f1: the frame classifies as the expected
  guest (Dana arrives that day), the run logs it through the
  auto_close gate, and nobody is notified. The motion play never
  messages a guest.
- Gathering accumulation (case #6, batch 3): "Camera motion — one
  crowded frame" sends frame f3 alone — about eleven people on the
  patio (a model estimate) against Alex's booked party of four —
  and it LOGS: one frame never fires the gathering notification.
  "Camera motion — gathering sustained across three frames" sends
  f26, f27, then f3 (9:20, 9:35, 9:48 PM). The first two log; f3
  completes the sustained run and flags the OPERATOR, held at the
  notify_operator gate: "at least 9 people (model estimate,
  sustained across 3 frames at 3 moments over 28 minutes)" against
  a threshold of 8 (booked 4 + margin 4), with the frame references
  and a not-a-headcount line. The sustained floor is the largest N
  that frames at at least 3 distinct minutes (two cameras at the
  same minute are one moment), spread over at least 20 minutes
  inside a rolling 90-minute window, each estimate; the margin is max(4,
  ceil(booked / 2)). A repeat click finds the stay already flagged
  and logs — the stay is told again only if the floor grows by
  another full margin. Frames are staged metadata (jev.plays.vision.STAGED_FRAMES),
  classified by the fixture vision implementation; the live vision
  path was verified against the real API on 2026-10-27 (see
  vision.py for the confirmed request shape) but the sandbox never
  uses it.
- Camera offline (the switch play): "Camera offline — switch
  flipped during a stay" runs the offline extension at Garden
  Unit 1A, where Alex is mid-stay and the front-door camera is on
  the staged switch-fed list: the run drafts the camera system's
  one guest message — flip the switch by the front door back on,
  with the lights_and_camera wording shown in the step — and the
  send is HELD at the message_guest gate. With no stay in progress,
  or a camera that is not switch-fed, the same event drafts no
  guest message at all and flags the operator instead; and if the
  camera comes back before the held message executes, the run
  closes as resolved (POST /event, camera_online).
- Vacant mode: "Camera motion — person at the closed house" uses
  frame f5 at Maple Street House on 2026-10-30, a date the fixtures
  mark closed: any person notifies the operator, and the summary
  carries the frame reference with the still attached in the
  simulated channel. A worker inside a scheduled work window on
  that date (frame f6, via the API) still logs quietly.
- Post-turnover state check: "Post-turnover check — door open"
  uses frame f7: the classification's door/lights fields (an
  optional, strictly validated extension of the vision schema)
  show the door open at an emptied home, so the operator is
  flagged. "All secured" (frame f8: door closed, lights off)
  logs only. A frame that cannot settle the state flags the
  operator too.
- Camera covered (final-list case #17): "Camera covered —
  one-night weekend stay" uses frame f10: the classification's
  view field reads blocked (a second strictly validated optional
  field on the same schema), so the operator is flagged whatever
  the scene says — and because Sam's stay at Garden Unit 1A that
  Friday is a single night, the notification is marked urgent.
  A merely unclear frame never counts as covered; the view has
  to read blocked or shifted explicitly (frame f11, via the API,
  is the shifted variant at Studio 2A).
- Guest lockout (case #10): "Guest lockout — person at the shut
  door" reports a lockout at Studio 2A and pins frame f13: the
  operator's notification carries the exact, small claim — a
  person is at the door and the door reads shut — plus the frame
  reference. Nothing is sent to the guest. The same play has a
  motion-side twin: frames f12 then f13 through the camera-check
  endpoint (two POSTs, minutes apart, person at the shut door
  inside the arrival window) complete the dwell pattern and the
  second run flags the operator with the stuck-at-the-door
  reason; frame f14 (door open) breaks the pattern and logs.
- Guest feels unsafe (case #49): "Guest feels unsafe" assembles
  Studio 2A's latest three frames — references plus their staged
  classifications, nothing more claimed — into one URGENT
  operator notification. At a property with no frames on record
  for the date (Garden Unit 1A on 2026-10-28, via the API), the
  notification says exactly that: the absence is the information.
- Sunset check (case #11): "Sunset check — entry dark, second
  night running" uses frame f16 at Studio 2A on Dana's arrival
  day: the entry lights read off, and because the fixtures carry
  one dark night already, the notification adds the bulb change
  to the next turnover list. Frame f15 (Maple Street House, via the
  API) is a first dark night — flagged, no bulb line; frame f17
  (lights on) logs only; and with no arrival that day the check
  logs whatever it reads.
- Dog with no pet on the booking (case #46): "Camera motion —
  dog arriving with the party" uses frame f19 at Maple Street House
  on Marcus's arrival day: the classification's animal field
  reads dog_sized (a third strictly validated optional field on
  the same schema), the party is in the arrival window, and the
  booking lists no pet — so the operator is flagged with the
  still and the exact small print: nothing has been charged,
  service animals are exempt, the call is the operator's. Frame
  f20 (via the API) is the control: a dog-sized animal crossing
  the driveway alone at night logs quietly, and a booking that
  lists a pet (a fixture variant) logs too.
- Morning snow check (case #32): "Morning check — snow still
  down at an empty home" uses frame f21 at Maple Street House on
  2026-10-29, a day with no stay, arrival, or turnover there:
  the classification's ground field reads snow_covered past the
  10 AM deadline, so the operator is flagged to send the
  shoveler. Frame f22 (Studio 2A, via the API) is the urgent
  variant — a guest and a turnover are due that day. Frame f23
  (clear) logs; frame f24 (ground unsettled) flags, fail safe;
  and at an occupied home (frame f25, Alex mid-stay) the check
  skips quietly by design.
- Area outage (case #36): "Area outage — three cameras drop at
  once" fires three camera_offline events inside eleven minutes
  (two Cedar Hollow properties, three cameras, one shared area
  label). Each runs the ordinary offline play; when the third
  lands, the correlation layer forms the cluster, marks each
  individual run with the cluster_id in its own record, and the
  response adds the single area-outage run — one operator
  notification listing the affected properties instead of three
  urgent alerts. Below the threshold (fewer cameras, one
  property, a wider window) nothing changes. A cluster touching
  a vacant property in a freezing month adds the freeze-risk
  line to the notification.
- Camera upkeep (case #58): "Camera upkeep — fleet self-check"
  reads the staged camera history in the fixtures and produces
  the turnover notes the evidence supports: Garden Unit 1A's
  front-door camera went offline around 7 AM on three days
  ("check the WiFi"), Studio 2A's front-door camera fires night
  motion that is almost all nothing-or-animal, and Maple Street
  House's driveway camera classifies most frames unclear ("wipe
  the lens" twice over). 1A's backyard camera is staged below
  every threshold and earns no note. The report goes to the
  operator only; nothing is sent to a guest or a cleaner.
- Vendor proof of service (case #23, batch 3): "Vendor proof of
  service — worker on camera inside the stated window" opens a run
  on the vendor's stated 10 AM–noon window at Maple Street House
  (closed that day), then delivers the scheduled close. Frame f6
  (10:30 AM) reads as a worker — the classification's worker field,
  a strictly validated optional field on the same schema — so a
  proof-of-service log entry is written with the frame reference.
  It is presence evidence only: it never says who the person was,
  or anything about the quality or completion of the work. "Window
  closed, no vendor seen" runs the 3–4 PM window for Alex's leak at
  Garden Unit 1A: the one frame inside it (f28) shows an empty
  walkway, so the operator gets a held notification that no vendor
  was seen inside the stated window — the absence of a sighting,
  not proof the visit did not happen. Frames that cannot be read
  are never sightings; a window with no frames says so plainly.
- Move-in signs (case #55, batch 3): "Camera motion — boxes carried
  in on a third day" sends Jordan's long stay at Studio 2A in
  order: f29 (arrival-day suitcases — the carrying field reads
  luggage, which never counts), then f30, f31, f32 — boxes or
  belongings carried in on three distinct days. The first frames
  log; f32 raises ONE awareness-only operator notification, held,
  worded as signs consistent with a possible move-in, never that
  one is happening. Frame f33 (via the API, later that evening)
  logs: the stay is already flagged, and it is never flagged
  twice.
- In-stay issue: "Guest reports a leak — Garden Unit 1A" runs
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
- Late checkout (Play 5, the late-checkout variant of special
  requests): both buttons send the same guest message ("check out at
  1pm instead of 11?") for the same departure day, 2026-11-08,
  against two calendars. "Garden Unit 1A, nobody arriving" is a clear
  day: Riley departs, nobody arrives, so 1 PM is a yes, and because
  1 PM is past the free first hour the late checkout fee is its own
  step, HELD at the highest-stakes charge_guest gate next to the held
  guest reply. The fee amount is the operator's to set: with
  JEV_LATE_CHECKOUT_FEE_USD unset (the default), the reply and the
  fee step say "amount to be set" and carry no dollar figure; with it
  set, that amount is quoted on the held step. "Maple Street House,
  new guests arriving" is a same-day turnover: Casey departs, Morgan
  checks in at 4 PM, and
  the 180-minute turnover plus the 30-minute buffer has to fit
  first, so 12:30 PM is the latest workable checkout. The drafted
  reply is a no to 1 PM that offers 12:30 PM, and no charge step is
  drafted for a time the guest has not accepted. Via the API, the
  same play also covers: a request with no time stated (a clarifying
  question back, carrying the latest workable time and the fee
  line), an ask past the house's latest-checkout rule on a clear day
  (a yes to the latest time instead), an ask that fits inside the
  free hour (no fee step), a grant that moves a same-day turnover's
  start (a cleaner heads-up, held at message_cleaner, without the
  cleaner's name in it), and an unknown property or no stay
  departing that day (held for a human, nothing drafted).
