"""Play: CAMERA CHECK (Play 7).

An exterior camera reports motion. The play classifies the frame,
checks the classification against what the calendar says should be
happening at that property, and then either logs the event or flags
it to the OPERATOR. It never messages a guest. Ever — no step in
this play carries a guest-facing action key, by design.

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
"""
from datetime import datetime

from ..types import Decision
from .context import fmt_time, to_minutes
from .early_checkin import _effect
from .engine import Play, PlayRun, Step
from .vision import Classification, FixtureVision


def _ts_minutes(ts: str) -> int | None:
    """Minutes since midnight from an ISO timestamp; None if the
    timestamp cannot be read (checks that need it then stay lenient
    where the plan allows, strict where it does not)."""
    try:
        dt = datetime.fromisoformat(str(ts).strip())
    except ValueError:
        return None
    return dt.hour * 60 + dt.minute


def _route_for(cls: Classification | None, exp: dict,
               event_minutes: int | None) -> tuple[str, str]:
    """(route, reason): route is 'log' or 'notify'. The bias is
    deliberate: anything the play cannot reconcile with reality goes
    to the operator; only the reconciled and the trivial are logged."""
    if cls is None:
        return "notify", "the camera frame could not be classified"
    scene = cls.scene
    if scene == "unclear":
        return "notify", "the camera frame was unclear"
    if scene == "unknown_person":
        return "notify", "a person was seen who matches nobody expected"
    if scene in ("nothing_notable", "animal", "vehicle"):
        return "log", f"{scene.replace('_', ' ')} — nothing that needs a human"
    if scene == "expected_guest":
        if exp["arrival_guest"] or exp["stay_guest"]:
            return "log", "matches the guest expected at this property"
        return "notify", ("someone arrived like a guest, but no arrival "
                          "or stay is expected at this property")
    if scene == "cleaner":
        if not exp["cleaner"]:
            return "notify", ("a cleaner was seen, but no turnover is "
                              "scheduled at this property today")
        window = exp["cleaner_window"]
        if event_minutes is None or window is None \
                or window[0] <= event_minutes <= window[1]:
            return "log", "matches the cleaner scheduled at this property"
        return "notify", "a cleaner was seen outside the expected cleaning window"
    if scene == "large_gathering":
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
        # calls. LiveVision exists in vision.py as UNVERIFIED-LIVE.
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
        exp = {
            "property_name": prop.name,
            "arrival_guest": arrival.guest_name if arrival else "",
            "stay_guest": stay.guest_name if stay else "",
            "expected_party": (arrival.guest_count if arrival else
                               stay.guest_count if stay else None),
            "cleaner": turn.cleaner_name if turn else "",
            "cleaner_window": window,
        }
        run.data["expectation"] = exp
        step.result = {
            "property": prop.name,
            "arrival_today": arrival is not None,
            "mid_stay": stay is not None,
            "cleaner_expected": exp["cleaner"],
            "expected_party": exp["expected_party"],
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
        route, reason = _route_for(cls, exp, _ts_minutes(event.get("ts", "")))
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
        step.result = {"summary": summary,
                       "scene": cls.scene if cls else "unclassified",
                       "reason": run.data["flag_reason"],
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
