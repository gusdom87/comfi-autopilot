# Camera support boundary (production, Oct 29, 2026)

This is the production support boundary for Comfi Autopilot's camera
features. It is not code built during the event. No camera connector of any
kind is built in this repo, and nothing here claims one is.

What this build does today: every camera play in the sandbox runs on staged
frame metadata and staged camera status from the fixtures, classified by the
fixture vision implementation. The sandbox never fetches from a camera. The
live vision path was verified against the Meta Model API on Oct 27, 2026
with a test image; that is a vision check, not a camera integration. A
production connector would have to supply the frames and the camera status
history that the fixtures stand in for today.

## The rule

A camera brand by itself is never a support promise. Support is listed for an
exact camera model, hardware revision, firmware, power type, and connector
version, and only after that combination passes the certification checks
below. Vendors change firmware and cloud APIs, so no honest list can promise
that every future version will keep working.

## What a production connector must provide

The camera plays need all of this from a connector to run the full feature
set:

1. Motion-triggered frames.
2. A fresh frame on demand, for the scheduled checks (post-turnover, sunset,
   morning).
3. Repeated frames at separate times, for the patterns that live across
   frames: a gathering that grows, a person stuck at the door, a vendor
   inside a stated window, and move-in signs across days.
4. The latest available frames on request, right away, when a guest reports
   feeling unsafe.
5. Camera online and offline events, plus enough retained history to detect
   repeated drops, area outages, and upkeep patterns.
6. A stable camera identity mapped to one property and one view.
7. Frames clear enough, day and night, for the existing frame gate and the
   vision classifier. A failed or unclear frame stays a fail-safe to a human,
   never a guessed sighting.

A continuous RTSP stream can meet the frame requirements when a local
recorder (Frigate, for example) supplies the motion events, snapshots, clips,
and stream-loss detection. An event-image connector can meet them only if it
also provides on-demand frames and reliable status, not just a motion
thumbnail.

## Full-feature candidate classes

These classes are eligible for the full camera feature set. Eligible means a
candidate for certification, not a support promise: the exact model and
firmware still has to pass the checks below before it is listed.

### 1. Open IP cameras (ONVIF or RTSP)

- PoE or plug-in powered cameras that expose a continuous RTSP stream and
  ONVIF Profile S or T, or an equivalent documented local snapshot and event
  API.
- Dahua, plus EmpireTech and Loryta Dahua models.
- Hikvision wired IP cameras, plus Hikvision-made Annke models, each model
  individually.
- Amcrest wired IP cameras. Cloud-only Amcrest Smart Home models are not
  covered by the brand name.
- Axis network cameras through ONVIF or VAPIX.
- Reolink PoE and plug-in Wi-Fi cameras that expose RTSP, ONVIF, or the
  documented HTTP stream for the exact model. Reolink behavior varies by model
  and generation, so exact-model testing is required. Reolink battery cameras
  are excluded (see the limited list below).
- TP-Link Tapo wired and plug-in cameras that expose RTSP and ONVIF, exact
  model only. Tapo battery models are excluded.
- Foscam, Hanwha, Uniview, Bosch, and other ONVIF IP cameras, exact model
  only.
- Selected powered Eufy models whose exact model exposes RTSP, such as the
  Outdoor Cam C22 and C24 and the Floodlight Cam 2 Pro, exact model only.
  This is not a brand-level claim for Eufy.

### 2. NVR-fed cameras

- Camera channels from a tested NVR (Dahua, Hikvision, Amcrest, Uniview, or
  another tested NVR), when each channel is a separate continuous stream and
  the connector can also get per-camera motion and connection status.
  Compatibility belongs to the tested NVR and camera combination, not to the
  camera alone.

### 3. UniFi Protect

- UniFi Protect cameras through a UniFi OS Protect console, using the Protect
  public API, RTSPS streams, snapshots, motion or smart-detection events, and
  camera availability. This is an ecosystem connector through the console,
  not direct ONVIF access to each camera.

### 4. Wyze through Wyze Bridge

Selected powered Wyze models are candidates through docker-wyze-bridge.
Wyze Bridge is community software that uses Wyze account access. It is not a
Wyze-published integration and not vendor-guaranteed. A tested setup can be
certified, but it is never described as an official Wyze integration.

Candidate models:

- Wyze Cam v3, v3 Pro, and v4.
- Wyze Cam Floodlight and Floodlight V2.
- Wyze Cam Pan, Pan v2, Pan v3, and Pan Pro.
- Wyze Doorbell and Doorbell v2, only when wired for continuous power.

Excluded from the full feature set:

- Wyze Cam Outdoor and Outdoor v2: battery models, excluded even though the
  bridge lists stream support for them.
- Wyze Cam OG and OG Telephoto, Battery Cam Pro, Floodlight Pro, and Doorbell
  Pro: not listed as supported in the current Wyze Bridge table. Excluded
  until that changes and a unit passes certification.
- Wyze Cam v1 and v2: legacy models, not in the full-feature list.

## Not full support

These paths are not promised for the full camera feature set. At most, a
specific model could be listed separately for limited event-image support
after its own certification.

- Blink through Home Assistant: event images, clips, and motion state only.
  No live view in Home Assistant, no standard RTSP or ONVIF stream, and
  battery polling limits.
- Ring: no native RTSP. Community software can reach streams or snapshots in
  some configurations, with battery snapshot limits. Not an official or
  full-support path.
- Arlo: no RTSP, ONVIF, or local stream endpoint. Community cloud integrations
  only. Not a full-support path.
- Google Nest, which varies by model. Legacy cameras with SDM snapshot support
  give event images only, and each image has to be fetched promptly. Newer
  models whose SDM support is a WebRTC stream plus motion or person events,
  with no snapshot, are not full-feature candidates.
- Reolink battery cameras, including through a Reolink Home Hub: the hub
  opens third-party protocols, but the camera still sleeps and stream
  sessions are time-limited. Event-limited.
- Any battery camera that sleeps and cannot give a fresh frame when asked.
- Any camera whose only outside access is its own mobile app.

## Certification checks

A model is listed as supported only after all of these pass on a real unit:

1. Enroll the camera and map it to a test property.
2. Trigger motion and receive a usable frame.
3. Request a fresh frame with no motion present.
4. Collect frames at three separate minutes and run the multi-frame cases.
5. Disconnect the camera and receive an offline state; reconnect it and
   receive an online state.
6. Repeat an offline and online cycle and confirm the retained history.
7. Run a night check and confirm the frame passes the existing frame gate or
   fails safe as unclear.
8. Record the exact model, hardware revision, firmware, power type,
   connector, and connector version with the passing result.

## Claim-safe wording

Use this, or narrower:

"Camera features are supported on certified ONVIF or RTSP camera systems,
UniFi Protect, and selected powered Wyze models through Wyze Bridge. Exact
model and firmware are verified during setup. Battery-powered and app-only
cameras are not supported for the full camera feature set unless a specific
model is separately listed with limited event-image support."

For this build specifically, add: the camera plays run on staged frames and
staged camera status, and no camera connector is built yet.

Do not use:

- "Works with any camera."
- "Works with Blink, Ring, Nest, and Arlo" without a support tier.
- "Works with Wyze" without naming Wyze Bridge and the model limits.
- "Works with Reolink, Tapo, Amcrest, or Eufy" as a blanket brand promise.
- Any wording that says this build watches a live home or connects to a
  camera today.
