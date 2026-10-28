"""Play: CAMERA CHECK (Play 7) and its Tier-1 extensions.

An exterior camera reports motion. The play classifies the frame,
checks the classification against what the calendar says should be
happening at that property, and then either logs the event or flags
it to the OPERATOR. The motion play never messages a guest — no
step in CameraCheck carries a guest-facing action key, by design.

Event-triggered only, like Play 3: a triage Decision never starts
it. handle_camera_event() below is the entry point, on the same
wait-step rails Play 3 uses.

Decision rules (the ladder decides what a route means):
  - nothing notable, an animal, a vehicle, or people who match who
    is expected (the arriving guest, the cleaner inside the cleaning
    window, a gathering within the booked party size) -> LOG ONLY,
    through the auto_close gate (low stakes).
  - an unknown person, a gathering bigger than the booking expects,
    people who match nobody expected, or an unclear frame -> NOTIFY
    OPERATOR, an action gated at middle stakes (notify_operator).
    At P1 the notification is HELD with its summary preserved;
    nothing reaches anyone until the right is earned or a human
    approves it.
  - vision that cannot classify at all (no staged frame, a live
    failure, an unparsable reply) is treated exactly like an unclear
    frame: fail safe to the operator, never waved through.
  - an event for a property with no reality data holds the run for
    a human, like every other play.
  - VACANT MODE: when the property is marked closed for the date,
    the expectation flips — nobody should be there. Any person-class
    scene notifies the operator, with the frame reference in the
    summary (the still rides the simulated notification channel).
    Only a worker inside a scheduled work window (the turnover's
    cleaning window, or a fixture work window) still logs.
  - a person seen while the cleaning window is open who is not the
    cleaner notifies with a specific warning — the departing guest
    may not have left yet. That is Play 3's protection, seen from
    the camera side.

Two sibling plays share this module's rails, vision, and gates:

  - CAMERA OFFLINE (the switch play): a camera_offline event during
    a stay, for a camera on the property's known switch-fed list,
    drafts ONE guest message — the only guest-facing step in the
    whole camera system — asking the guest to flip the switch back
    on. It is gated at message_guest and held at P1 like every
    guest send. The wording is a per-run choice (`mention`:
    lights_and_camera | lights_only); the operator decides which a
    real message would use. No stay, or a camera that is not
    switch-fed, means no guest message at all — the operator gets a
    maintenance/connectivity flag instead. If the camera comes back
    before the held message ever executes, the run closes as
    resolved (handle_camera_online_event).
  - POST-TURNOVER STATE CHECK: a scheduled post_turnover event
    after the cleaning window. The frame's door/lights state is the
    classification that matters: door open or lights on at a home
    with no stay in progress notifies the operator; door closed and
    lights off logs; anything the frame cannot settle notifies too.

The final-list batch (Gus's roadmap of record, Oct 27) adds four
more cases on the same rails:

  - CAMERA COVERED OR TURNED (case #17, inside CameraCheck): the
    classification's view field reads blocked or shifted -> the
    operator is notified regardless of scene. A merely unclear
    frame never counts — the view must read blocked or shifted
    explicitly. On a one-night weekend stay the notification is
    marked urgent.
  - STUCK AT THE DOOR (case #10): (a) a guest lockout report
    (GuestLockout) assembles the door camera's latest frame and
    its door state for the operator — it never messages the guest;
    (b) a dwell pattern inside CameraCheck: a person at the door,
    door shut, inside the arrival window, still there on a second
    frame minutes later -> the same operator notification. The
    claim language is exact and small: a person is at the door
    and the door reads shut. Never an identity, never a reason.
  - GUEST FEELS UNSAFE (case #49, GuestUnsafe): a guest reports
    someone outside or knocking; the operator gets the property's
    latest frames, marked urgent. Retrieval only — the frames'
    existing classifications are all it says. No frames on record
    is stated plainly in the notification: the absence is itself
    the information.
  - DARK ENTRY (case #11, SunsetCheck): a scheduled sunset_check
    on an arrival day reads the entry frame's lights field. Dark
    with a guest arriving notifies the operator; a second
    consecutive dark night adds the bulb change to the next
    turnover list. No arrival that day logs only.
"""
from datetime import date as _date_cls
from datetime import datetime

from ..types import Decision
from .context import fmt_time, to_minutes
from .early_checkin import _effect
from .engine import Play, PlayRun, Step
from .vision import Classification, FixtureVision, frames_for


def _ts_minutes(ts: str) -> int | None:
    """Minutes since midnight from an ISO timestamp; None if the
    timestamp cannot be read (checks that need it then stay lenient
    where the plan allows, strict where it does not)."""
    try:
        dt = datetime.fromisoformat(str(ts).strip())
    except ValueError:
        return None
    return dt.hour * 60 + dt.minute


# Stuck-at-the-door dwell memory (case #10b). A single frame of a
# person at a door is unremarkable; the pattern is the SAME read —
# person present, door shut, inside the arrival window — on a
# second frame minutes later. The play keeps the qualifying frames'
# minutes per (property, date, camera) so a later frame can check
# them. In-process and short-lived by construction: the staged
# stand-in for the recent-frame store a real camera system keeps.
_DOOR_FRAMES: dict[tuple, list[int]] = {}
DWELL_MIN_GAP_MINUTES = 5       # "several minutes", not a blip
DWELL_MAX_GAP_MINUTES = 90      # beyond this it is a new episode
_PERSON_SCENES = ("expected_guest", "unknown_person")


def _reset_door_frames() -> None:
    """Test hook: clear the dwell memory between scenarios."""
    _DOOR_FRAMES.clear()


def _dwell_reason(run: PlayRun, event: dict,
                  cls: Classification | None, exp: dict,
                  event_minutes: int | None) -> str | None:
    """The stuck-at-the-door read for this frame, or None. Side
    effect: a qualifying frame (person scene, door shut, arrival
    window active) is recorded whether or not it fires, so the
    NEXT frame can complete the pattern."""
    if cls is None or event_minutes is None:
        return None
    if cls.scene not in _PERSON_SCENES or cls.door != "closed":
        return None
    window = exp.get("arrival_window")
    if window is None or not (window[0] <= event_minutes <= window[1]):
        return None
    key = (run.property_id, run.date, str(event.get("camera", "")))
    prior = [t for t in _DOOR_FRAMES.get(key, [])
             if DWELL_MIN_GAP_MINUTES <= event_minutes - t
             <= DWELL_MAX_GAP_MINUTES]
    _DOOR_FRAMES.setdefault(key, []).append(event_minutes)
    if not prior:
        return None
    return ("a person is at the door and the door reads shut — a "
            "frame minutes earlier showed the same, inside the "
            "arrival window (stuck-at-the-door pattern)")


def _is_one_night_weekend(stay, date_str: str) -> bool:
    """True when the stay covering this date is a single night and
    that night is a Friday or Saturday — the stay shape where a
    compromised camera (case #17) matters most."""
    if stay is None or not stay.arrival_date or not stay.departure_date:
        return False
    try:
        nights = (_date_cls.fromisoformat(stay.departure_date)
                  - _date_cls.fromisoformat(stay.arrival_date)).days
        weekday = _date_cls.fromisoformat(date_str).weekday()
    except ValueError:
        return False
    return nights == 1 and weekday in (4, 5)   # Friday, Saturday


def _route_for(cls: Classification | None, exp: dict,
               event_minutes: int | None) -> tuple[str, str]:
    """(route, reason): route is 'log' or 'notify'. The bias is
    deliberate: anything the play cannot reconcile with reality goes
    to the operator; only the reconciled and the trivial are logged."""
    if cls is None:
        return "notify", "the camera frame could not be classified"
    if cls.view in ("blocked", "shifted"):
        # Case #17: the camera itself is compromised. That outranks
        # the scene — a covered camera makes every other read on
        # this frame worthless, including a trivial one. Only an
        # explicit blocked/shifted read lands here; an unclear
        # frame's view stays "unknown" and routes on its scene.
        return "notify", (f"the camera view reads {cls.view} — the "
                          f"camera itself needs attention")
    scene = cls.scene
    if scene == "unclear":
        return "notify", "the camera frame was unclear"
    vacant = bool(exp.get("vacant"))
    if scene == "unknown_person":
        if vacant:
            return "notify", ("a person was seen at a property that is "
                              "closed and should be vacant")
        window = exp.get("cleaner_window")
        if window is not None and event_minutes is not None \
                and window[0] <= event_minutes <= window[1]:
            # Not the cleaner, inside the cleaning window: the
            # departing guest may still be in the home. Play 3 must
            # not hear "ready" into a house that is not empty.
            return "notify", ("a person was still at the property when "
                              "the cleaning window opened — the guest "
                              "may not have left yet")
        return "notify", "a person was seen who matches nobody expected"
    if scene in ("nothing_notable", "animal", "vehicle"):
        return "log", f"{scene.replace('_', ' ')} — nothing that needs a human"
    if scene == "expected_guest":
        if vacant:
            return "notify", ("a person arrived at a property that is "
                              "closed and should be vacant")
        if exp["arrival_guest"] or exp["stay_guest"]:
            return "log", "matches the guest expected at this property"
        return "notify", ("someone arrived like a guest, but no arrival "
                          "or stay is expected at this property")
    if scene == "cleaner":
        if exp.get("work_window_active"):
            return "log", ("matches a scheduled work window at this "
                           "property")
        if not exp["cleaner"]:
            return "notify", ("a cleaner was seen, but no turnover is "
                              "scheduled at this property today")
        window = exp["cleaner_window"]
        if event_minutes is None or window is None \
                or window[0] <= event_minutes <= window[1]:
            return "log", "matches the cleaner scheduled at this property"
        return "notify", "a cleaner was seen outside the expected cleaning window"
    if scene == "large_gathering":
        if vacant:
            return "notify", ("a gathering was seen at a property that "
                              "is closed and should be vacant")
        party = exp["expected_party"]
        if party is not None and cls.people_count is not None \
                and cls.people_count <= party:
            return "log", "the gathering is within the booked party size"
        return "notify", "more people at the property than the booking expects"
    return "notify", "unrecognized scene"      # unreachable; fail safe


class CameraCheck(Play):
    name = "camera_check"
    description = "An exterior camera reports motion: classify the " \
                  "frame, check it against the calendar, log what is " \
                  "expected and flag the operator about what is not."

    def __init__(self, vision=None):
        # FixtureVision by default: staged classifications, zero live
        # calls. LiveVision is verified live (see vision.py); the
        # sandbox and the tests still run the staged path.
        self.vision = vision if vision is not None else FixtureVision()

    def trigger(self, decision: Decision, message: str, context: dict) -> bool:
        # Event-triggered, not message-triggered: a triage Decision
        # never starts this play. Entry point is the camera-motion
        # event (see handle_camera_event).
        return False

    def build(self, run: PlayRun) -> list[Step]:
        return [
            Step("wait_camera_motion", "wait", event="camera_motion"),
            Step("match_reality", "internal", run=self._match_reality),
            Step("classify_frame", "internal", run=self._classify),
            Step("log_event", "action", run=self._log_event,
                 action_key="auto_close",
                 skip_if=lambda r: r.data.get("route") != "log"),
            Step("notify_operator", "action", run=self._notify,
                 action_key="notify_operator",
                 skip_if=lambda r: r.data.get("route") != "notify"),
        ]

    # -- helpers -------------------------------------------------------
    def _event(self, run: PlayRun) -> dict:
        return run.data.get("event:camera_motion", {})

    # -- steps ---------------------------------------------------------
    def _match_reality(self, engine, run: PlayRun, step: Step) -> None:
        ctx = engine.context
        prop = ctx.get_property(run.property_id)
        if prop is None:
            step.status = "failed"
            step.result = {"error": "no reality data for property"}
            run.hold_reason = "camera event for an unknown property"
            return
        arrival = ctx.get_arrival(run.property_id, run.date)
        stay = ctx.get_stay(run.property_id, run.date)
        turn = ctx.get_turnover(run.property_id, run.date)
        window = None
        if turn is not None:
            window = (to_minutes(turn.checkout_time),
                      turn.expected_finish(prop))
        event = self._event(run)
        event_minutes = _ts_minutes(event.get("ts", ""))
        work_windows = []
        if hasattr(ctx, "get_work_windows"):
            work_windows = list(ctx.get_work_windows(run.property_id,
                                                     run.date))
        work_active = event_minutes is not None and any(
            to_minutes(w.start) <= event_minutes <= to_minutes(w.end)
            for w in work_windows)
        vacant = bool(ctx.is_closed(run.property_id, run.date)) \
            if hasattr(ctx, "is_closed") else False
        arrival_window = None
        if arrival is not None:
            checkin = to_minutes(arrival.checkin_time) \
                if arrival.checkin_time \
                else to_minutes(prop.standard_checkin)
            # The window a guest plausibly arrives (and can get
            # stuck) in: the hours around the check-in time.
            arrival_window = (checkin - 180, checkin + 60)
        exp = {
            "property_name": prop.name,
            "arrival_guest": arrival.guest_name if arrival else "",
            "stay_guest": stay.guest_name if stay else "",
            "expected_party": (arrival.guest_count if arrival else
                               stay.guest_count if stay else None),
            "cleaner": turn.cleaner_name if turn else "",
            "cleaner_window": window,
            "vacant": vacant,
            "work_window_active": work_active,
            "work_windows": [w.label for w in work_windows],
            "arrival_window": arrival_window,
            "one_night_weekend": _is_one_night_weekend(stay, run.date),
        }
        run.data["expectation"] = exp
        step.result = {
            "property": prop.name,
            "arrival_today": arrival is not None,
            "mid_stay": stay is not None,
            "cleaner_expected": exp["cleaner"],
            "expected_party": exp["expected_party"],
            "vacant": vacant,
            "work_window_active": work_active,
            "work_windows": exp["work_windows"],
            "one_night_weekend": exp["one_night_weekend"],
        }

    def _classify(self, engine, run: PlayRun, step: Step) -> None:
        event = self._event(run)
        frame_id = str(event.get("frame_id", ""))
        try:
            cls = self.vision.classify(
                frame_id, image_bytes=run.data.get("frame_bytes"))
        except Exception:               # vision is external; fail safe
            cls = None
        exp = run.data["expectation"]
        event_minutes = _ts_minutes(event.get("ts", ""))
        route, reason = _route_for(cls, exp, event_minutes)
        dwell = _dwell_reason(run, event, cls, exp, event_minutes)
        if dwell is not None:
            # Case #10b: the pattern outranks the single-frame
            # route (an expected guest at a shut door would
            # otherwise just log).
            route, reason = "notify", dwell
        run.data.update({
            "classification": cls, "route": route,
            "outcome": "logged" if route == "log" else "notify_operator",
            "flag_reason": reason,
        })
        step.result = {
            "frame_id": frame_id,
            "camera": str(event.get("camera", "")),
            "scene": cls.scene if cls else "unclassified",
            "people_count": cls.people_count if cls else None,
            "note": cls.note if cls else "",
            "vision_source": cls.source if cls else "",
            "door": cls.door if cls else "unknown",
            "lights": cls.lights if cls else "unknown",
            "view": cls.view if cls else "unknown",
            "route": route, "reason": reason,
        }

    def _log_event(self, engine, run: PlayRun, step: Step) -> None:
        cls: Classification | None = run.data["classification"]
        step.result = {
            "logged": True,
            "scene": cls.scene if cls else "",
            "reason": run.data["flag_reason"],
            "note": "logged only — no notification, no message to anyone",
            **_effect(step),
        }

    def _notify(self, engine, run: PlayRun, step: Step) -> None:
        event = self._event(run)
        exp = run.data["expectation"]
        cls: Classification | None = run.data["classification"]
        minutes = _ts_minutes(event.get("ts", ""))
        when = fmt_time(minutes) if minutes is not None \
            else str(event.get("ts", ""))
        if cls is None:
            seen = "the frame could not be classified at all"
        elif cls.scene == "unclear":
            seen = "the frame was too dark or unclear to identify anything"
        else:
            seen = f"the camera saw: {cls.scene.replace('_', ' ')}"
            if cls.people_count:
                seen += f" ({cls.people_count} people)"
            if cls.note:
                seen += f" — {cls.note}"
        context_bits = []
        if exp.get("vacant"):
            context_bits.append("the property is closed and should "
                                "be vacant today")
        if exp["arrival_guest"]:
            context_bits.append(f"{exp['arrival_guest']} is arriving today")
        if exp["stay_guest"]:
            context_bits.append(
                f"{exp['stay_guest']} is mid-stay "
                f"(booked party of {exp['expected_party']})")
        if exp["cleaner"]:
            context_bits.append(
                f"{exp['cleaner']} is scheduled to clean today")
        context_line = "; ".join(context_bits) if context_bits \
            else "nobody is expected at this property today"
        summary = (
            f"Camera check — {exp['property_name']}, "
            f"{event.get('camera', 'exterior')} camera, {when}: {seen}. "
            f"Flagged because {run.data['flag_reason']}. "
            f"Booking context: {context_line}. "
            f"Nothing was sent to the guest."
        )
        frame_id = str(event.get("frame_id", ""))
        if exp.get("vacant") and frame_id:
            # The owner gets the still, not just words: the frame
            # reference rides the notification and the simulated
            # channel carries the image itself.
            summary += (f" Still: frame {frame_id} — a still from "
                        f"that frame is attached in the simulated "
                        f"notification channel.")
        urgent = bool(cls is not None
                      and cls.view in ("blocked", "shifted")
                      and exp.get("one_night_weekend"))
        if urgent:
            # Case #17's extra weight: a compromised camera on a
            # one-night weekend stay is the pattern that precedes
            # the worst nights in this business.
            summary += (" One-night weekend stay — treat as urgent: "
                        "the camera is compromised on the stay "
                        "shape where that matters most.")
        step.result = {"summary": summary,
                       "scene": cls.scene if cls else "unclassified",
                       "reason": run.data["flag_reason"],
                       "frame_id": frame_id,
                       "vacant": bool(exp.get("vacant")),
                       "view": cls.view if cls else "unknown",
                       "urgent": urgent,
                       **_effect(step)}


def handle_camera_event(engine, payload: dict,
                        play: CameraCheck | None = None) -> PlayRun:
    """Entry point: an exterior-camera motion event arrived (webhook
    shape: property_id, date, camera, ts, frame_id). Creates the
    Play 7 run on the shared rails — the run parks on its wait step —
    then delivers the event, which wakes it and runs the play out.
    Malformed events (no property, no date, no frame) are a caller
    error and raise; everything after that fails safe inside the
    run. `play` may be passed to run a play instance carrying a
    different vision implementation (tests, live experiments)."""
    property_id = str(payload.get("property_id", "")).strip()
    date = str(payload.get("date", "")).strip()
    frame_id = str(payload.get("frame_id", "")).strip()
    if not property_id or not date or not frame_id:
        raise ValueError(
            "camera_motion event needs property_id, date and frame_id")
    run = engine.start(play or CameraCheck(), None,
                       f"[event] camera_motion {property_id} {date} "
                       f"{frame_id}",
                       property_id, date)
    engine.handle_event(run, "camera_motion", payload)
    return run


# ---------------------------------------------------------------------
# Camera offline: the switch play (Play 7 extension)
# ---------------------------------------------------------------------

MENTION_VARIANTS = ("lights_and_camera", "lights_only")


class CameraOffline(Play):
    """A camera went offline. If a guest is in residence and the
    camera is one of the property's known switch-fed cameras, the
    likely story is the light switch — and the one guest-facing step
    in the camera system asks that guest, in the operator's chosen
    wording, to flip it back on. Every other shape (no stay, a
    camera that is not switch-fed) is not a guest matter at all: the
    operator gets a maintenance/connectivity flag."""

    name = "camera_offline"
    description = "A camera went offline: if a stay is in progress " \
                  "and the camera is switch-fed, ask the guest to " \
                  "flip the switch back on; otherwise flag the " \
                  "operator."

    def __init__(self, mention: str = "lights_and_camera"):
        mention = str(mention or "lights_and_camera").strip()
        if mention not in MENTION_VARIANTS:
            raise ValueError(
                f"mention must be one of {MENTION_VARIANTS}")
        self.mention = mention

    def trigger(self, decision: Decision, message: str, context: dict) -> bool:
        # Event-triggered, like the motion play: entry point is the
        # camera_offline event (see handle_camera_offline_event).
        return False

    def build(self, run: PlayRun) -> list[Step]:
        guest = lambda r: r.data.get("route") != "guest"
        operator = lambda r: r.data.get("route") != "operator"
        return [
            Step("wait_camera_offline", "wait", event="camera_offline"),
            Step("check_reality", "internal", run=self._check_reality),
            Step("guest_switch_message", "action", run=self._guest_message,
                 action_key="message_guest", skip_if=guest),
            Step("notify_operator", "action", run=self._notify,
                 action_key="notify_operator", skip_if=operator),
            Step("close_out", "internal", run=self._close_out),
        ]

    # -- helpers -------------------------------------------------------
    def _event(self, run: PlayRun) -> dict:
        return run.data.get("event:camera_offline", {})

    # -- steps ---------------------------------------------------------
    def _check_reality(self, engine, run: PlayRun, step: Step) -> None:
        ctx = engine.context
        event = self._event(run)
        camera = str(event.get("camera", "")).strip()
        mention = str(event.get("mention", "")).strip() or self.mention
        if mention not in MENTION_VARIANTS:
            step.status = "failed"
            step.result = {"error": f"unknown mention variant: {mention}"}
            run.hold_reason = "camera-offline event carried a bad " \
                              "wording variant"
            return
        prop = ctx.get_property(run.property_id)
        if prop is None:
            step.status = "failed"
            step.result = {"error": "no reality data for property"}
            run.hold_reason = "camera-offline event for an unknown property"
            return
        stay = ctx.get_stay(run.property_id, run.date)
        switch_fed = camera in tuple(prop.switch_cameras or ())
        if stay is not None and switch_fed:
            route = "guest"
            reason = ("a stay is in progress and this camera is on "
                      "the property's known switch-fed list")
        elif stay is None:
            route = "operator"
            reason = ("the camera went offline with no stay in "
                      "progress — not a guest matter")
        else:
            route = "operator"
            reason = ("this camera is not on the property's known "
                      "switch-fed list, so a flipped switch does not "
                      "explain it")
        run.data.update({
            "route": route, "flag_reason": reason, "stay": stay,
            "property_name": prop.name, "switch_fed": switch_fed,
            "mention": mention, "camera": camera,
        })
        step.result = {
            "property": prop.name, "camera": camera,
            "stay_in_progress": stay is not None,
            "guest": stay.guest_name if stay else "",
            "switch_fed": switch_fed, "mention": mention,
            "route": route, "reason": reason,
        }

    def _guest_message(self, engine, run: PlayRun, step: Step) -> None:
        prop = engine.context.get_property(run.property_id)
        stay = run.data["stay"]
        mention = run.data["mention"]
        location = prop.switch_location or "by the front door, inside"
        restores = ("the entrance lights and the outside camera"
                    if mention == "lights_and_camera"
                    else "the entrance lights")
        text = (
            f"Hi {stay.guest_name}, quick favor — the switch "
            f"{location} seems to have been flipped off, and that "
            f"switch powers {restores}, so they are out right now. "
            f"Could you please flip it back on? Thank you so much "
            f"for your time, and for doing this."
        )
        run.data["outcome"] = "switch_message_held" \
            if step.disposition == "hold" else "switch_message_sent"
        step.result = {"draft": text, "guest": stay.guest_name,
                       "mention": mention,
                       "switch_location": location,
                       "camera": run.data["camera"],
                       **_effect(step)}

    def _notify(self, engine, run: PlayRun, step: Step) -> None:
        stay = run.data["stay"]
        summary = (
            f"Camera offline — {run.data['property_name']}, "
            f"{run.data['camera']} camera, {run.date}: flagged "
            f"because {run.data['flag_reason']}. "
            f"Switch-fed: {'yes' if run.data['switch_fed'] else 'no'}; "
            f"stay in progress: "
            f"{stay.guest_name if stay else 'none'}. "
            f"No guest message was drafted. Treat this as a "
            f"maintenance / connectivity check."
        )
        run.data["outcome"] = "operator_flagged"
        step.result = {"summary": summary,
                       "reason": run.data["flag_reason"],
                       "camera": run.data["camera"],
                       **_effect(step)}

    def _close_out(self, engine, run: PlayRun, step: Step) -> None:
        step.result = {"route": run.data.get("route", ""),
                       "outcome": run.data.get("outcome", "")}


def handle_camera_offline_event(engine, payload: dict,
                                play: CameraOffline | None = None) -> PlayRun:
    """Entry point: a camera reported itself offline (event shape:
    property_id, date, camera, optional ts, optional mention in
    {lights_and_camera, lights_only}). Creates the camera-offline
    run on the shared rails and delivers the event. Malformed events
    are a caller error and raise; everything after that fails safe
    inside the run."""
    property_id = str(payload.get("property_id", "")).strip()
    date = str(payload.get("date", "")).strip()
    camera = str(payload.get("camera", "")).strip()
    if not property_id or not date or not camera:
        raise ValueError(
            "camera_offline event needs property_id, date and camera")
    play = play or CameraOffline(
        mention=str(payload.get("mention", "")).strip()
        or "lights_and_camera")
    run = engine.start(play, None,
                       f"[event] camera_offline {property_id} {date} "
                       f"{camera}",
                       property_id, date)
    engine.handle_event(run, "camera_offline", payload)
    return run


def handle_camera_online_event(engine, run: PlayRun,
                               payload: dict | None = None) -> PlayRun:
    """The camera came back. If the run's outward step was only HELD
    (P1: nothing executed), the incident closes as resolved — the
    message will never send, because there is nothing left to ask.
    If the step already executed through the simulated channel, the
    event is recorded but the outcome stands: a sent message cannot
    be unsent, and the log must not pretend otherwise."""
    payload = payload or {}
    engine.handle_event(run, "camera_online", payload)
    if run.play != CameraOffline.name or run.status != "completed":
        return run
    outward = next((s for s in run.steps
                    if s.name in ("guest_switch_message",
                                  "notify_operator")
                    and s.status in ("held", "done")), None)
    if outward is None or outward.result.get("executed") is not False:
        return run
    run.data["outcome"] = "resolved_camera_back"
    run.data["resolved"] = True
    step = Step("resolve_camera_online", "internal")
    step.status = "done"
    step.result = {
        "resolved": True,
        "note": ("the camera came back before the held step ever "
                 "executed — nothing was sent, the run closes as "
                 "resolved"),
        "camera": str(payload.get("camera", "") or run.data.get("camera", "")),
    }
    run.steps.append(step)
    engine._log(run, "run_resolved", outcome="resolved_camera_back",
                step=outward.name)
    return run


# ---------------------------------------------------------------------
# Post-turnover state check (Play 7 extension)
# ---------------------------------------------------------------------

class PostTurnoverCheck(Play):
    """A scheduled check after the cleaning window: is the emptied
    home actually as it should be? The frame's door/lights state is
    what matters here, not the scene. Door open or lights on with
    nobody in residence notifies the operator; door closed and
    lights off logs; a state the frame cannot settle notifies too —
    "probably fine" is not a state this play acts on."""

    name = "camera_post_turnover"
    description = "After a turnover, check the camera frame's door " \
                  "and lights state against an emptied home."

    def __init__(self, vision=None):
        self.vision = vision if vision is not None else FixtureVision()

    def trigger(self, decision: Decision, message: str, context: dict) -> bool:
        # Event-triggered only: the scheduled post_turnover event.
        return False

    def build(self, run: PlayRun) -> list[Step]:
        return [
            Step("wait_post_turnover", "wait", event="post_turnover"),
            Step("match_reality", "internal", run=self._match_reality),
            Step("classify_state", "internal", run=self._classify_state),
            Step("log_event", "action", run=self._log_event,
                 action_key="auto_close",
                 skip_if=lambda r: r.data.get("route") != "log"),
            Step("notify_operator", "action", run=self._notify,
                 action_key="notify_operator",
                 skip_if=lambda r: r.data.get("route") != "notify"),
        ]

    # -- helpers -------------------------------------------------------
    def _event(self, run: PlayRun) -> dict:
        return run.data.get("event:post_turnover", {})

    # -- steps ---------------------------------------------------------
    def _match_reality(self, engine, run: PlayRun, step: Step) -> None:
        ctx = engine.context
        prop = ctx.get_property(run.property_id)
        if prop is None:
            step.status = "failed"
            step.result = {"error": "no reality data for property"}
            run.hold_reason = "post-turnover check for an unknown property"
            return
        stay = ctx.get_stay(run.property_id, run.date)
        report = ctx.get_clean_report(run.property_id, run.date)
        run.data.update({
            "property_name": prop.name, "stay": stay,
            "clean_status": report.status if report else "",
        })
        step.result = {
            "property": prop.name,
            "stay_in_progress": stay is not None,
            "guest": stay.guest_name if stay else "",
            "clean_status": run.data["clean_status"],
        }

    def _classify_state(self, engine, run: PlayRun, step: Step) -> None:
        event = self._event(run)
        frame_id = str(event.get("frame_id", ""))
        try:
            cls = self.vision.classify(
                frame_id, image_bytes=run.data.get("frame_bytes"))
        except Exception:               # vision is external; fail safe
            cls = None
        stay = run.data["stay"]
        if cls is None:
            route, reason = "notify", ("the camera frame could not be "
                                       "classified")
            door = lights = "unknown"
        elif stay is not None:
            route, reason = "log", ("a stay is in progress — door and "
                                    "lights state is the guest's own "
                                    "business right now")
            door, lights = cls.door, cls.lights
        elif cls.door == "open" or cls.lights == "on":
            route = "notify"
            seen = []
            if cls.door == "open":
                seen.append("the door is open")
            if cls.lights == "on":
                seen.append("the lights are on")
            reason = (" and ".join(seen)
                      + " at a home with no stay in progress")
            door, lights = cls.door, cls.lights
        elif cls.door == "closed" and cls.lights == "off":
            route, reason = "log", ("door closed and lights off — the "
                                    "home is as it should be")
            door, lights = cls.door, cls.lights
        else:
            route, reason = "notify", ("the frame could not settle "
                                       "the door/lights state")
            door, lights = cls.door, cls.lights
        run.data.update({
            "classification": cls, "route": route,
            "outcome": "logged" if route == "log" else "notify_operator",
            "flag_reason": reason, "door": door, "lights": lights,
        })
        step.result = {
            "frame_id": frame_id,
            "camera": str(event.get("camera", "")),
            "scene": cls.scene if cls else "unclassified",
            "door": door, "lights": lights,
            "note": cls.note if cls else "",
            "vision_source": cls.source if cls else "",
            "route": route, "reason": reason,
        }

    def _log_event(self, engine, run: PlayRun, step: Step) -> None:
        step.result = {
            "logged": True, "door": run.data["door"],
            "lights": run.data["lights"],
            "reason": run.data["flag_reason"],
            "note": "logged only — no notification, no message to anyone",
            **_effect(step),
        }

    def _notify(self, engine, run: PlayRun, step: Step) -> None:
        event = self._event(run)
        frame_id = str(event.get("frame_id", ""))
        summary = (
            f"Post-turnover check — {run.data['property_name']}, "
            f"{event.get('camera', 'exterior')} camera, {run.date}: "
            f"door {run.data['door']}, lights {run.data['lights']}. "
            f"Flagged because {run.data['flag_reason']}. "
            f"Clean on record: {run.data['clean_status'] or 'none'}. "
            f"Nothing was sent to the guest."
        )
        if frame_id:
            summary += f" Frame: {frame_id}."
        step.result = {"summary": summary, "door": run.data["door"],
                       "lights": run.data["lights"],
                       "reason": run.data["flag_reason"],
                       "frame_id": frame_id,
                       **_effect(step)}


def handle_post_turnover_event(engine, payload: dict,
                               play: PostTurnoverCheck | None = None) -> PlayRun:
    """Entry point: the scheduled post-turnover check fired (event
    shape: property_id, date, kind="post_turnover", camera, ts,
    frame_id). Creates the run on the shared rails and delivers the
    event. Malformed events are a caller error and raise; everything
    after that fails safe inside the run."""
    property_id = str(payload.get("property_id", "")).strip()
    date = str(payload.get("date", "")).strip()
    frame_id = str(payload.get("frame_id", "")).strip()
    kind = str(payload.get("kind", "post_turnover")).strip()
    if not property_id or not date or not frame_id:
        raise ValueError(
            "post_turnover event needs property_id, date and frame_id")
    if kind != "post_turnover":
        raise ValueError(f"unknown scheduled-check kind: {kind}")
    run = engine.start(play or PostTurnoverCheck(), None,
                       f"[event] post_turnover {property_id} {date} "
                       f"{frame_id}",
                       property_id, date)
    engine.handle_event(run, "post_turnover", payload)
    return run


# ---------------------------------------------------------------------
# Guest lockout: stuck at the door, report side (final-list case #10a)
# ---------------------------------------------------------------------

def _classify_guarded(vision, frame_id: str) -> Classification | None:
    try:
        return vision.classify(frame_id)
    except Exception:               # vision is external; fail safe
        return None


def _door_word(cls: Classification | None) -> str:
    """What the frame settles about the door, in the notification's
    words: 'shut', 'open', or an honest not-settled."""
    if cls is None:
        return "could not be settled"
    return {"closed": "shut", "open": "open"}.get(cls.door,
                                                  "could not be settled")


def _person_at_door(cls: Classification | None) -> bool:
    if cls is None:
        return False
    if cls.scene in _PERSON_SCENES or cls.scene == "large_gathering":
        return True
    return bool(cls.people_count)


class GuestLockout(Play):
    """A guest reports they cannot get in. The play assembles what
    the door camera last saw — the latest frame reference for the
    property's door camera and its staged door state — and hands it
    to the OPERATOR as a notification, so the human helping the
    guest works from the picture, not a guess. This play sends
    nothing to the guest: no code, no reassurance, nothing. The
    language stays exact and small — a person is at the door and
    the door reads shut (or open, or unsettled, as the frame says).
    Never an identity, never a claim about why they are stuck."""

    name = "guest_lockout"
    description = "A guest reports a lockout: assemble the door " \
                  "camera's latest frame and door state for the " \
                  "operator."

    def __init__(self, vision=None):
        self.vision = vision if vision is not None else FixtureVision()

    def trigger(self, decision: Decision, message: str, context: dict) -> bool:
        # Event-triggered only: the guest_lockout event (raised when
        # the guest-message side reports a lockout).
        return False

    def build(self, run: PlayRun) -> list[Step]:
        return [
            Step("wait_guest_lockout", "wait", event="guest_lockout"),
            Step("assemble_door_frame", "internal",
                 run=self._assemble),
            Step("notify_operator", "action", run=self._notify,
                 action_key="notify_operator"),
        ]

    # -- helpers -------------------------------------------------------
    def _event(self, run: PlayRun) -> dict:
        return run.data.get("event:guest_lockout", {})

    # -- steps ---------------------------------------------------------
    def _assemble(self, engine, run: PlayRun, step: Step) -> None:
        ctx = engine.context
        event = self._event(run)
        prop = ctx.get_property(run.property_id)
        if prop is None:
            step.status = "failed"
            step.result = {"error": "no reality data for property"}
            run.hold_reason = "lockout report for an unknown property"
            return
        arrival = ctx.get_arrival(run.property_id, run.date)
        stay = ctx.get_stay(run.property_id, run.date)
        camera = str(event.get("camera", "")).strip() or "front-door"
        # The frame the report is checked against: the event names
        # it (a live webhook would), else the latest door-camera
        # frame on record at or before the report's timestamp.
        frame_id = str(event.get("frame_id", "")).strip()
        if not frame_id:
            event_minutes = _ts_minutes(event.get("ts", ""))
            candidates = frames_for(run.property_id, run.date, camera)
            if event_minutes is not None:
                candidates = [r for r in candidates
                              if (_ts_minutes(r["ts"]) or 0)
                              <= event_minutes] or candidates
            if candidates:
                frame_id = candidates[-1]["frame_id"]
        cls = _classify_guarded(self.vision, frame_id) if frame_id \
            else None
        run.data.update({
            "property_name": prop.name, "camera": camera,
            "frame_id": frame_id, "classification": cls,
            "guest": (arrival.guest_name if arrival else
                      stay.guest_name if stay else ""),
            "context_kind": ("arriving today" if arrival else
                             "mid-stay" if stay else "none on record"),
        })
        step.result = {
            "property": prop.name, "camera": camera,
            "frame_id": frame_id,
            "scene": cls.scene if cls else "unclassified",
            "door": cls.door if cls else "unknown",
            "note": cls.note if cls else "",
            "guest": run.data["guest"],
            "context": run.data["context_kind"],
        }

    def _notify(self, engine, run: PlayRun, step: Step) -> None:
        cls: Classification | None = run.data["classification"]
        frame_id = run.data["frame_id"]
        if not frame_id or cls is None:
            seen = ("no usable camera frame is on record for this "
                    "door today, so there is nothing to check the "
                    "report against")
        elif _person_at_door(cls):
            seen = (f"latest frame {frame_id}: a person is at the "
                    f"door and the door reads {_door_word(cls)}")
            if cls.note:
                seen += f" — {cls.note}"
        else:
            seen = (f"latest frame {frame_id} does not clearly show "
                    f"a person at the door (scene read: "
                    f"{cls.scene.replace('_', ' ')}; the door reads "
                    f"{_door_word(cls)})")
        if run.data["guest"]:
            context_line = (f"{run.data['guest']} is "
                            f"{run.data['context_kind']} at this "
                            f"property")
        else:
            context_line = ("no arrival or stay is on record for "
                            "this property today — verify who is "
                            "reporting before acting")
        summary = (
            f"Guest lockout — {run.data['property_name']}, "
            f"{run.data['camera']} camera, {run.date}: the guest "
            f"reports they cannot get in. {seen[0].upper()}"
            f"{seen[1:]}. Booking context: {context_line}. "
            f"Nothing was sent to the guest."
        )
        run.data["outcome"] = "notify_operator"
        step.result = {"summary": summary,
                       "frame_id": frame_id,
                       "door": cls.door if cls else "unknown",
                       "scene": cls.scene if cls else "unclassified",
                       **_effect(step)}


def handle_guest_lockout_event(engine, payload: dict,
                               play: GuestLockout | None = None) -> PlayRun:
    """Entry point: the guest-message side reports a lockout (event
    shape: property_id, date, optional camera, ts, frame_id).
    Creates the run on the shared rails and delivers the event.
    Malformed events are a caller error and raise; everything after
    that fails safe inside the run."""
    property_id = str(payload.get("property_id", "")).strip()
    date = str(payload.get("date", "")).strip()
    if not property_id or not date:
        raise ValueError("guest_lockout event needs property_id and date")
    run = engine.start(play or GuestLockout(), None,
                       f"[event] guest_lockout {property_id} {date}",
                       property_id, date)
    engine.handle_event(run, "guest_lockout", payload)
    return run


# ---------------------------------------------------------------------
# Guest feels unsafe (final-list case #49)
# ---------------------------------------------------------------------

class GuestUnsafe(Play):
    """A guest messages that someone is outside, or knocking, or
    that they feel unsafe. Seconds matter and the operator should
    not have to go looking: the play assembles the property's
    latest frames and hands them over in one urgent notification.
    Retrieval only — the notification says what the frames' existing
    classifications say and nothing more. If no frames are on
    record, the notification says so plainly: the absence is itself
    the information the operator needs."""

    name = "guest_unsafe"
    description = "A guest feels unsafe: assemble the property's " \
                  "latest camera frames for the operator, urgently."

    def __init__(self, vision=None):
        self.vision = vision if vision is not None else FixtureVision()

    def trigger(self, decision: Decision, message: str, context: dict) -> bool:
        # Event-triggered only: the guest_unsafe event (raised when
        # a guest message is classified upstream as an unsafe report).
        return False

    def build(self, run: PlayRun) -> list[Step]:
        return [
            Step("wait_guest_unsafe", "wait", event="guest_unsafe"),
            Step("assemble_latest_frames", "internal",
                 run=self._assemble),
            Step("notify_operator", "action", run=self._notify,
                 action_key="notify_operator"),
        ]

    # -- helpers -------------------------------------------------------
    def _event(self, run: PlayRun) -> dict:
        return run.data.get("event:guest_unsafe", {})

    # -- steps ---------------------------------------------------------
    def _assemble(self, engine, run: PlayRun, step: Step) -> None:
        ctx = engine.context
        prop = ctx.get_property(run.property_id)
        if prop is None:
            step.status = "failed"
            step.result = {"error": "no reality data for property"}
            run.hold_reason = "unsafe report for an unknown property"
            return
        stay = ctx.get_stay(run.property_id, run.date)
        arrival = ctx.get_arrival(run.property_id, run.date)
        records = frames_for(run.property_id, run.date)[-3:]
        frames = []
        for rec in reversed(records):       # newest first
            cls = _classify_guarded(self.vision, rec["frame_id"])
            minutes = _ts_minutes(rec["ts"])
            frames.append({
                "frame_id": rec["frame_id"], "camera": rec["camera"],
                "ts": rec["ts"],
                "when": fmt_time(minutes) if minutes is not None
                else rec["ts"],
                "scene": cls.scene if cls else "unclassified",
                "note": cls.note if cls else "",
            })
        run.data.update({
            "property_name": prop.name, "frames": frames,
            "guest": (stay.guest_name if stay else
                      arrival.guest_name if arrival else ""),
        })
        step.result = {
            "property": prop.name,
            "frames": [f["frame_id"] for f in frames],
            "frame_count": len(frames),
            "guest": run.data["guest"],
        }

    def _notify(self, engine, run: PlayRun, step: Step) -> None:
        frames = run.data["frames"]
        if frames:
            listing = "; ".join(
                f"{f['frame_id']} ({f['camera']}, {f['when']}): "
                f"{f['scene'].replace('_', ' ')}"
                + (f" — {f['note']}" if f["note"] else "")
                for f in frames)
            seen = f"Latest camera frames: {listing}."
        else:
            seen = ("No camera frames are on record for this "
                    "property today — that absence is itself the "
                    "information: there is nothing to check the "
                    "report against.")
        guest_line = (f" Guest in residence: {run.data['guest']}."
                      if run.data["guest"] else "")
        summary = (
            f"URGENT — guest feels unsafe — "
            f"{run.data['property_name']}, {run.date}: the guest "
            f"reports someone outside or knocking.{guest_line} "
            f"{seen} Nothing was sent to the guest; the guest is "
            f"waiting on the operator."
        )
        run.data["outcome"] = "notify_operator"
        step.result = {"summary": summary, "urgent": True,
                       "frames": [f["frame_id"] for f in frames],
                       "frame_count": len(frames),
                       **_effect(step)}


def handle_guest_unsafe_event(engine, payload: dict,
                              play: GuestUnsafe | None = None) -> PlayRun:
    """Entry point: a guest message was classified upstream as an
    unsafe report (event shape: property_id, date,
    kind="guest_unsafe"). Creates the run on the shared rails and
    delivers the event. Malformed events are a caller error and
    raise; everything after that fails safe inside the run."""
    property_id = str(payload.get("property_id", "")).strip()
    date = str(payload.get("date", "")).strip()
    kind = str(payload.get("kind", "guest_unsafe")).strip()
    if not property_id or not date:
        raise ValueError("guest_unsafe event needs property_id and date")
    if kind != "guest_unsafe":
        raise ValueError(f"unknown unsafe-report kind: {kind}")
    run = engine.start(play or GuestUnsafe(), None,
                       f"[event] guest_unsafe {property_id} {date}",
                       property_id, date)
    engine.handle_event(run, "guest_unsafe", payload)
    return run


# ---------------------------------------------------------------------
# Dark entry: the sunset check (final-list case #11)
# ---------------------------------------------------------------------

class SunsetCheck(Play):
    """A scheduled check after sunset on an arrival day: is the
    entry lit for the guest who is coming? The frame's lights field
    is the read. Dark with a guest arriving notifies the operator
    BEFORE the guest pulls up; a second consecutive dark night adds
    the bulb change to the next turnover list. Lights on logs. No
    arrival that day logs too — a dark entry with nobody coming is
    a note, not a flag, and it does not touch the dark-night
    streak. A frame that cannot settle the lights notifies: the
    check exists so a dark entry is never assumed lit."""

    name = "camera_sunset_check"
    description = "After sunset on an arrival day, check the entry " \
                  "frame's lights against the arriving guest."

    def __init__(self, vision=None):
        self.vision = vision if vision is not None else FixtureVision()

    def trigger(self, decision: Decision, message: str, context: dict) -> bool:
        # Event-triggered only: the scheduled sunset_check event.
        return False

    def build(self, run: PlayRun) -> list[Step]:
        return [
            Step("wait_sunset_check", "wait", event="sunset_check"),
            Step("match_reality", "internal", run=self._match_reality),
            Step("classify_entry", "internal", run=self._classify_entry),
            Step("log_event", "action", run=self._log_event,
                 action_key="auto_close",
                 skip_if=lambda r: r.data.get("route") != "log"),
            Step("notify_operator", "action", run=self._notify,
                 action_key="notify_operator",
                 skip_if=lambda r: r.data.get("route") != "notify"),
        ]

    # -- helpers -------------------------------------------------------
    def _event(self, run: PlayRun) -> dict:
        return run.data.get("event:sunset_check", {})

    # -- steps ---------------------------------------------------------
    def _match_reality(self, engine, run: PlayRun, step: Step) -> None:
        ctx = engine.context
        prop = ctx.get_property(run.property_id)
        if prop is None:
            step.status = "failed"
            step.result = {"error": "no reality data for property"}
            run.hold_reason = "sunset check for an unknown property"
            return
        arrival = ctx.get_arrival(run.property_id, run.date)
        run.data.update({
            "property_name": prop.name, "arrival": arrival,
            "dark_streak_before": ctx.dark_nights(run.property_id)
            if hasattr(ctx, "dark_nights") else 0,
        })
        step.result = {
            "property": prop.name,
            "arrival_today": arrival is not None,
            "guest": arrival.guest_name if arrival else "",
            "dark_streak_before": run.data["dark_streak_before"],
        }

    def _classify_entry(self, engine, run: PlayRun, step: Step) -> None:
        ctx = engine.context
        event = self._event(run)
        frame_id = str(event.get("frame_id", ""))
        cls = _classify_guarded(self.vision, frame_id) if frame_id \
            else None
        arrival = run.data["arrival"]
        lights = cls.lights if cls else "unknown"
        streak = run.data["dark_streak_before"]
        second_night = False
        if arrival is None:
            route, reason = "log", ("no arrival today — a dark or "
                                    "lit entry is noted, not flagged")
        elif cls is None:
            route, reason = "notify", ("the entry frame could not "
                                       "be classified")
        elif lights == "off":
            if hasattr(ctx, "note_dark_night"):
                streak = ctx.note_dark_night(run.property_id, True)
            else:
                streak = streak + 1
            second_night = streak >= 2
            route = "notify"
            reason = ("the entry is dark after sunset with a guest "
                      "arriving today")
            if second_night:
                reason += " — second night running"
        elif lights == "on":
            if hasattr(ctx, "note_dark_night"):
                streak = ctx.note_dark_night(run.property_id, False)
            else:
                streak = 0
            route, reason = "log", ("the entry lights are on for "
                                    "the arriving guest")
        else:
            route, reason = "notify", ("the frame could not settle "
                                       "the entry lights")
        run.data.update({
            "classification": cls, "route": route, "lights": lights,
            "outcome": "logged" if route == "log" else "notify_operator",
            "flag_reason": reason, "dark_streak": streak,
            "second_night": second_night,
        })
        step.result = {
            "frame_id": frame_id,
            "camera": str(event.get("camera", "")),
            "scene": cls.scene if cls else "unclassified",
            "lights": lights,
            "note": cls.note if cls else "",
            "dark_streak": streak, "second_night": second_night,
            "route": route, "reason": reason,
        }

    def _log_event(self, engine, run: PlayRun, step: Step) -> None:
        step.result = {
            "logged": True, "lights": run.data["lights"],
            "reason": run.data["flag_reason"],
            "note": "logged only — no notification, no message to anyone",
            **_effect(step),
        }

    def _notify(self, engine, run: PlayRun, step: Step) -> None:
        event = self._event(run)
        arrival = run.data["arrival"]
        frame_id = str(event.get("frame_id", ""))
        summary = (
            f"Dark entry — {run.data['property_name']}, "
            f"{event.get('camera', 'exterior')} camera, {run.date}: "
            f"entry lights read {run.data['lights']}. Flagged "
            f"because {run.data['flag_reason']}. "
            f"{arrival.guest_name if arrival else 'A guest'} is "
            f"arriving today — the entry is dark before the guest "
            f"arrives. Nothing was sent to the guest."
        )
        if run.data["second_night"]:
            summary += (" Second night running — bulb change "
                        "belongs on the next turnover list.")
        if frame_id:
            summary += f" Frame: {frame_id}."
        step.result = {"summary": summary, "lights": run.data["lights"],
                       "reason": run.data["flag_reason"],
                       "second_night": run.data["second_night"],
                       "dark_streak": run.data["dark_streak"],
                       "frame_id": frame_id,
                       **_effect(step)}


def handle_sunset_check_event(engine, payload: dict,
                              play: SunsetCheck | None = None) -> PlayRun:
    """Entry point: the scheduled sunset check fired (event shape:
    property_id, date, kind="sunset_check", camera, ts, frame_id).
    Creates the run on the shared rails and delivers the event.
    Malformed events are a caller error and raise; everything after
    that fails safe inside the run."""
    property_id = str(payload.get("property_id", "")).strip()
    date = str(payload.get("date", "")).strip()
    frame_id = str(payload.get("frame_id", "")).strip()
    kind = str(payload.get("kind", "sunset_check")).strip()
    if not property_id or not date or not frame_id:
        raise ValueError(
            "sunset_check event needs property_id, date and frame_id")
    if kind != "sunset_check":
        raise ValueError(f"unknown scheduled-check kind: {kind}")
    run = engine.start(play or SunsetCheck(), None,
                       f"[event] sunset_check {property_id} {date} "
                       f"{frame_id}",
                       property_id, date)
    engine.handle_event(run, "sunset_check", payload)
    return run
