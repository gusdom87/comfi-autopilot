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

Batch 2 of the final list adds four more, on the same rails:

  - DOG WITH NO PET ON THE BOOKING (case #46, inside CameraCheck):
    the classification's animal field reads dog_sized — a
    dog-sized animal arriving with the party, inside the arrival
    window or in a party scene, at a property whose booking lists
    no pet -> the operator is notified with the still reference
    and exact, small language: seen, no pet booked, nothing
    charged, service animals exempt, the operator's call. A lone
    animal at night never notifies; a pet on the booking never
    notifies; a small animal never notifies.
  - SNOW AT AN EMPTY HOME (case #32, MorningCheck): a scheduled
    morning_check reads the frame's ground field. An occupied
    home is skipped quietly — this case is only for empty homes.
    Snow still down at mid-morning notifies the operator ("send
    the shoveler"), urgent when a cleaner or guest is due that
    day. Clear logs; a ground state the frame cannot settle
    notifies, fail safe.
  - SEVERAL CAMERAS DROP AT ONCE (case #36): an area-correlation
    layer over camera_offline events. Recent offline events are
    recorded with their property's area label; when 3+ cameras
    across 2+ properties in one area drop inside 15 minutes, ONE
    area-outage notification (AreaOutage) goes to the operator
    and the individual offline runs are marked as part of the
    cluster instead of raising separate urgent alerts. Below the
    threshold, nothing changes. A cluster touching a vacant
    property in a freezing month carries the freeze-risk line.
  - CAMERA UPKEEP (case #58, CameraUpkeep): a periodic self-check
    over the fleet's own staged event history. A camera that went
    offline at roughly the same time on 3+ days earns a "check
    the WiFi" turnover note; a camera whose night motion is
    almost all nothing-or-animal, or whose frames repeatedly
    classify unclear, earns a "wipe the lens" note. Every note
    cites its evidence counts; the report goes to the operator
    only — nothing is ever sent to a guest or a cleaner.

Batch 3 of the final list adds three more, on the same rails:

  - GATHERING ACCUMULATION (case #6, inside CameraCheck): one frame
    no longer fires the gathering notification. Party-scene frames'
    people estimates accumulate per property and stay across a
    rolling window, and the operator is notified only when the
    SUSTAINED FLOOR (defined at GATHERING_SUSTAIN_FRAMES below)
    clears the booked party by the wide margin, max(4,
    ceil(booked / 2)). The notification says "at least N people
    (model estimate)" — never one frame's count, never a census.
    Below that, frames log. A gathering frame with no estimate at
    all cannot be checked against the booking, and says exactly
    that to the operator.
  - VENDOR PROOF OF SERVICE (case #23, VendorProof): anchored to
    the vendor's stated arrival window. When the window closes, the
    frames inside it are checked for worker presence: a sighting
    writes a proof-of-service log entry with the frame reference;
    no sighting notifies the operator that no vendor was seen
    inside the stated window. Presence only — never who the person
    was, never a word about the quality or completion of the work.
  - MOVE-IN SIGNS (case #55, inside CameraCheck): arrivals carrying
    boxes or household belongings on 3+ distinct days of one stay
    raise ONE awareness-only operator notification, worded as signs
    consistent with a possible move-in — never that a move-in is
    happening. Below that pattern, log only.
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


def _ts_abs_minutes(ts: str) -> int | None:
    """Absolute minute count from a full ISO timestamp (date ordinal
    * 1440 + minute of day), so windows compare across midnight — a
    party does not stop at 12. None if the timestamp cannot be read:
    a frame that cannot be placed in time accumulates nothing."""
    try:
        dt = datetime.fromisoformat(str(ts).strip())
    except ValueError:
        return None
    return dt.date().toordinal() * 1440 + dt.hour * 60 + dt.minute


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

# Dog-with-no-pet route (case #46). The language is exact and
# small on purpose: what was seen, what the booking says, and the
# two things that keep a human in charge of it — nothing has been
# charged, and service animals are exempt.
DOG_REASON = ("a dog-sized animal was seen arriving with the "
              "party; the booking lists no pet. Nothing has been "
              "charged; service animals are exempt — your call.")
_PARTY_SCENES = ("expected_guest", "large_gathering")


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


# Gathering accumulation (batch 3, case #6). One frame is one model
# guess — a group walking past, a double-counted shadow, one bad
# estimate — so it never fires the gathering notification alone.
# Party-scene frames' people estimates accumulate per property and
# stay, and the one claim the play makes rests on the SUSTAINED
# FLOOR:
#
#   the largest N such that frames at GATHERING_SUSTAIN_FRAMES or
#   more distinct minutes inside the rolling GATHERING_WINDOW_MINUTES
#   window each estimate N or more people, and those frames span at
#   least GATHERING_SUSTAIN_SPAN_MINUTES from the first to the last.
#
# A single peak frame cannot carry it (the frames around it hold
# the floor down to what was actually sustained); a burst of frames
# seconds apart cannot carry it (no span); two cameras catching the
# same minute are one moment, not two; and estimates are never
# summed across frames or cameras (the same people seen twice are
# not twice the people). The floor is what the notification claims
# — "at least N" — and it fires only when the floor clears the
# booked party by the wide margin, max(GATHERING_MARGIN_MIN,
# ceil(booked / 2)): never at booked, booked + 1, or booked +
# margin - 1; always at booked + margin once it is sustained.
GATHERING_WINDOW_MINUTES = 90
GATHERING_SUSTAIN_FRAMES = 3
GATHERING_SUSTAIN_SPAN_MINUTES = 20
GATHERING_MARGIN_MIN = 4
_COMPROMISED_VIEWS = ("blocked", "shifted")
# Per stay key: {"frames": {frame_id: (abs_minutes, estimate)},
# "notified_floor": the floor last notified, "sustained_at": the
# last time the floor stood at or over the threshold}. The same
# in-process stand-in for an event store as the dwell memory.
_GATHERINGS: dict[str, dict] = {}

# Move-in signs (batch 3, case #55): arrivals carrying boxes or
# belongings on this many distinct days of one stay. Any person
# scene counts (the claim is about what was carried, never who
# carried it); travel luggage never counts.
MOVE_IN_MIN_DAYS = 3
_MOVE_IN_SCENES = ("expected_guest", "unknown_person", "large_gathering")
# Per stay key: {"days": {date: [frame_id, ...]}, "notified": bool}.
_MOVE_INS: dict[str, dict] = {}


def _reset_gathering_frames() -> None:
    """Test hook: clear the gathering accumulation between scenarios."""
    _GATHERINGS.clear()


def _reset_move_in_signs() -> None:
    """Test hook: clear the move-in day record between scenarios."""
    _MOVE_INS.clear()


def gathering_margin(booked: int) -> int:
    """The wide margin a sustained estimate must clear the booked
    party by: max(GATHERING_MARGIN_MIN, ceil(booked / 2))."""
    return max(GATHERING_MARGIN_MIN, -(-booked // 2))


def sustained_floor(points) -> tuple[int, list] | None:
    """The sustained floor over (abs_minutes, estimate, frame_id)
    points, with its supporting points oldest first; None when no N
    is sustained (too few distinct minutes, or too little span)."""
    for n in sorted({est for _t, est, _fid in points}, reverse=True):
        support = sorted(p for p in points if p[1] >= n)
        if len({p[0] for p in support}) >= GATHERING_SUSTAIN_FRAMES \
                and support[-1][0] - support[0][0] \
                >= GATHERING_SUSTAIN_SPAN_MINUTES:
            return n, support
    return None


def _gathering_read(event: dict, cls: Classification | None,
                    exp: dict) -> dict | None:
    """Record this frame's people estimate toward its stay and read
    the sustained floor. None when the frame takes no part: no stay
    on record (nothing booked to measure against), a closed house
    (vacant mode speaks for itself), not a party scene, no estimate,
    a compromised view (every read on that frame is worthless), or
    a timestamp that cannot be placed in time. Otherwise the read:
    booked party, margin, threshold, floor and the frames behind it,
    and whether this frame fires (`fire`) or the stay's gathering
    was already flagged (`already_flagged`). Once notified, the
    stay is not notified again while the floor stays sustained,
    unless it grows by another full margin."""
    key = exp.get("stay_key")
    booked = exp.get("expected_party")
    if not key or exp.get("vacant") or cls is None:
        return None
    if cls.scene not in _PARTY_SCENES or cls.people_count is None \
            or cls.view in _COMPROMISED_VIEWS:
        return None
    if not isinstance(booked, int) or isinstance(booked, bool) \
            or booked < 0:
        return None
    now = _ts_abs_minutes(event.get("ts", ""))
    if now is None:
        return None
    state = _GATHERINGS.setdefault(
        key, {"frames": {}, "notified_floor": None, "sustained_at": None})
    frames = state["frames"]
    # Keyed by frame_id: a re-fired frame is one frame, not two.
    frames[str(event.get("frame_id", ""))] = (now, cls.people_count)
    # The window rolls with the newest evidence, so a frame that
    # arrives late still completes the run it belongs to.
    anchor = max(t for t, _n in frames.values())
    for fid in [f for f, (t, _n) in frames.items()
                if t < anchor - GATHERING_WINDOW_MINUTES]:
        del frames[fid]
    found = sustained_floor([(t, n, fid) for fid, (t, n) in frames.items()])
    floor, support = found if found is not None else (None, [])
    margin = gathering_margin(booked)
    read = {
        "booked": booked, "margin": margin, "threshold": booked + margin,
        "floor": floor,
        "frames": [{"frame_id": fid, "when": fmt_time(t % 1440)}
                   for t, _n, fid in support],
        "span_minutes": support[-1][0] - support[0][0] if support else 0,
        "moments": len({t for t, _n, _fid in support}),
        "frames_in_window": len(frames),
        "fire": False, "already_flagged": False,
    }
    if floor is None or floor < read["threshold"]:
        return read
    notified = state["notified_floor"]
    live = notified is not None and state["sustained_at"] is not None \
        and anchor - state["sustained_at"] <= GATHERING_WINDOW_MINUTES
    if not live or floor >= notified + margin:
        state["notified_floor"] = floor
        read["fire"] = True
    else:
        read["already_flagged"] = True
        read["notified_floor"] = notified
    state["sustained_at"] = anchor
    return read


_ONE_FRAME_REASON = ("one frame's estimate is above the booked party, "
                     "but a single frame never fires the gathering "
                     "notification — it counts toward the stay's "
                     "sustained estimate")


def _gathering_reason(read: dict) -> str:
    return (f"the stay's sustained people estimate clears the booked "
            f"party by the wide margin: at least {read['floor']} "
            f"(model estimate) against a booked party of "
            f"{read['booked']} — the notification threshold is "
            f"{read['threshold']} (booked {read['booked']} + margin "
            f"{read['margin']})")


def _move_in_read(run: PlayRun, event: dict, cls: Classification | None,
                  exp: dict) -> dict | None:
    """Record an arrival carrying boxes or belongings toward its
    stay's distinct days. None when the frame takes no part: no stay
    on record, a closed house, no person scene, a carrying read that
    is not boxes_or_belongings (luggage, none, or unknown), or a
    compromised view. Otherwise the read: days so far, the first
    frame on each, and whether this frame completes the pattern
    (`fire`) or the stay was already flagged — ONE notification per
    stay, ever."""
    key = exp.get("stay_key")
    if not key or exp.get("vacant") or cls is None:
        return None
    if cls.scene not in _MOVE_IN_SCENES \
            or cls.carrying != "boxes_or_belongings" \
            or cls.view in _COMPROMISED_VIEWS:
        return None
    state = _MOVE_INS.setdefault(key, {"days": {}, "notified": False})
    day = state["days"].setdefault(run.date, [])
    frame_id = str(event.get("frame_id", ""))
    if frame_id not in day:
        day.append(frame_id)
    dates = sorted(state["days"])
    read = {
        "days": len(dates), "min_days": MOVE_IN_MIN_DAYS,
        "frames": [{"date": d, "frame_id": state["days"][d][0]}
                   for d in dates],
        "fire": False, "already_flagged": False,
    }
    if len(dates) < MOVE_IN_MIN_DAYS:
        return read
    if state["notified"]:
        read["already_flagged"] = True
    else:
        state["notified"] = True
        read["fire"] = True
    return read


def _move_in_reason(read: dict) -> str:
    return (f"frames on {read['days']} distinct days of this stay "
            f"show arrivals carrying boxes or household belongings "
            f"(model reads) — signs consistent with a possible "
            f"move-in")


def _fold(route: str, reason: str, extra: str) -> tuple[str, str]:
    """Raise a pattern onto a frame's route: a frame already headed
    to the operator for its own reason carries both, so neither is
    silenced; a frame that would have logged now notifies."""
    if route == "notify":
        return "notify", f"{reason}; and {extra}"
    return "notify", extra


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
    # Case #46: a dog-sized animal arriving WITH the party. Every
    # condition is required — the animal field must read dog_sized
    # (a cat or raccoon is "small" and never lands here), a party
    # must actually be on the books (an arrival or a stay), the
    # booking must list no pet, and the moment must tie to the
    # party: inside the arrival window, or in the same frame as
    # the party itself. A lone animal at night satisfies none of
    # the party conditions and falls through to the trivial route.
    if cls.animal == "dog_sized" \
            and (exp.get("arrival_guest") or exp.get("stay_guest")) \
            and not exp.get("pet_on_booking"):
        window = exp.get("arrival_window")
        in_window = window is not None and event_minutes is not None \
            and window[0] <= event_minutes <= window[1]
        if in_window or scene in _PARTY_SCENES:
            return "notify", DOG_REASON
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
        if party is None:
            return "notify", ("a gathering was seen at a property with "
                              "no arrival or stay on record today")
        if cls.people_count is None:
            # Vision uncertainty: the scene reads as a gathering but
            # there is no estimate to measure. Fail safe to the
            # operator, and claim nothing about how many.
            return "notify", ("the frame reads as a gathering but "
                              "carries no people estimate, so it could "
                              "not be checked against the booked party "
                              "— look at the still")
        if cls.people_count <= party:
            return "log", "the gathering is within the booked party size"
        # Case #6: one frame never fires the gathering notification.
        # It counts toward the stay's sustained estimate, which
        # _classify reads and which alone can raise the route.
        return "log", _ONE_FRAME_REASON
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
        # The stay these frames belong to, for the patterns that
        # accumulate across frames (#6) and days (#55): the property
        # plus the stay's arrival date, so an arrival-day record and
        # the same stay's later records share one key, and two
        # bookings never mix. None with nothing booked.
        stay_key = (f"{run.property_id}|{arrival.date}" if arrival else
                    f"{run.property_id}|{stay.arrival_date or stay.date}"
                    if stay else None)
        # With no arrival date on record the key falls back to the
        # day itself, and days cannot be grouped across the stay.
        stay_key_per_day = arrival is None and stay is not None \
            and not stay.arrival_date
        exp = {
            "property_name": prop.name,
            "arrival_guest": arrival.guest_name if arrival else "",
            "stay_guest": stay.guest_name if stay else "",
            "pet_on_booking": (arrival.pet_on_booking if arrival else
                               stay.pet_on_booking if stay else False),
            "expected_party": (arrival.guest_count if arrival else
                               stay.guest_count if stay else None),
            "cleaner": turn.cleaner_name if turn else "",
            "cleaner_window": window,
            "vacant": vacant,
            "work_window_active": work_active,
            "work_windows": [w.label for w in work_windows],
            "arrival_window": arrival_window,
            "one_night_weekend": _is_one_night_weekend(stay, run.date),
            "stay_key": stay_key,
            "stay_key_per_day": stay_key_per_day,
        }
        run.data["expectation"] = exp
        step.result = {
            "property": prop.name,
            "arrival_today": arrival is not None,
            "mid_stay": stay is not None,
            "cleaner_expected": exp["cleaner"],
            "expected_party": exp["expected_party"],
            "pet_on_booking": exp["pet_on_booking"],
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
        dog_case = reason == DOG_REASON
        dwell = _dwell_reason(run, event, cls, exp, event_minutes)
        if dwell is not None:
            # Case #10b: the pattern outranks the single-frame
            # route (an expected guest at a shut door would
            # otherwise just log).
            route, reason = "notify", dwell
        # Cases #6 and #55: the cross-frame patterns. Every frame
        # that qualifies is recorded whatever it routes to; a
        # completed pattern raises the route (or rides along with
        # the frame's own reason), and a pattern already flagged
        # for this stay is noted on the log, never re-sent.
        gathering = _gathering_read(event, cls, exp)
        if gathering is None and reason == _ONE_FRAME_REASON \
                and _ts_abs_minutes(event.get("ts", "")) is None:
            reason += (" (not recorded: the frame's timestamp could not "
                       "be read)")
        move_in = _move_in_read(run, event, cls, exp)
        gathering_case = bool(gathering and gathering["fire"])
        move_in_case = bool(move_in and move_in["fire"])
        if gathering_case:
            route, reason = _fold(route, reason,
                                  _gathering_reason(gathering))
        elif gathering and gathering["already_flagged"] \
                and route == "log":
            reason += (f" (the gathering at this stay is already "
                       f"flagged to the operator: at least "
                       f"{gathering['notified_floor']} people, model "
                       f"estimate)")
        if move_in_case:
            route, reason = _fold(route, reason, _move_in_reason(move_in))
        elif move_in and route == "log":
            per_day = ("; the stay's arrival date is not on record, so "
                       "days cannot be grouped across this stay"
                       if exp.get("stay_key_per_day") else "")
            reason += (" (move-in signs at this stay are already "
                       "flagged — one notification per stay)"
                       if move_in["already_flagged"] else
                       f" (an arrival carrying boxes or belongings: "
                       f"{move_in['days']} of {MOVE_IN_MIN_DAYS} "
                       f"distinct days at this stay so far — logged "
                       f"only{per_day})")
        run.data.update({
            "classification": cls, "route": route,
            "outcome": "logged" if route == "log" else "notify_operator",
            "flag_reason": reason,
            "dog_case": dog_case,
            "gathering": gathering, "gathering_case": gathering_case,
            "move_in": move_in, "move_in_case": move_in_case,
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
            "animal": cls.animal if cls else "unknown",
            "carrying": cls.carrying if cls else "unknown",
            "gathering": gathering, "move_in": move_in,
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
        gathering = run.data["gathering"] if run.data.get("gathering_case") \
            else None
        move_in = run.data["move_in"] if run.data.get("move_in_case") \
            else None
        if cls is None:
            seen = "the frame could not be classified at all"
        elif cls.scene == "unclear":
            seen = "the frame was too dark or unclear to identify anything"
        elif gathering is not None:
            # Case #6: the claim is the sustained floor and nothing
            # else — not this frame's count, not its note (a note
            # can carry a number of its own), never a census.
            seen = (f"the camera saw: {cls.scene.replace('_', ' ')} — at "
                    f"least {gathering['floor']} people (model "
                    f"estimate, sustained across "
                    f"{len(gathering['frames'])} frames at "
                    f"{gathering['moments']} moments over "
                    f"{gathering['span_minutes']} minutes)")
        else:
            seen = f"the camera saw: {cls.scene.replace('_', ' ')}"
            if cls.people_count:
                # The count is the vision model's read, never a
                # proven headcount.
                seen += f" (about {cls.people_count} people, model estimate)"
            if cls.note:
                seen += f" — {cls.note.rstrip('.')}"
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
        if (exp.get("vacant") or run.data.get("dog_case")) and frame_id:
            # The owner gets the still, not just words: the frame
            # reference rides the notification and the simulated
            # channel carries the image itself. True for a person
            # at a closed house, and for the dog case — the
            # operator's fee-or-note call is made from the picture.
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
        if gathering is not None:
            refs = ", ".join(f"{f['frame_id']} ({f['when']})"
                             for f in gathering["frames"])
            summary += (f" Frames behind the estimate: {refs}. These "
                        f"are model estimates from camera frames, not "
                        f"a headcount — check the stills before "
                        f"acting.")
        if move_in is not None:
            refs = ", ".join(f"{f['frame_id']} ({f['date']})"
                             for f in move_in["frames"])
            summary += (f" Awareness only: these signs do not confirm "
                        f"a move-in — longer stays bring belongings "
                        f"for ordinary reasons. Frames behind the "
                        f"pattern: {refs}. No action has been taken.")
        step.result = {"summary": summary,
                       "scene": cls.scene if cls else "unclassified",
                       "reason": run.data["flag_reason"],
                       "frame_id": frame_id,
                       "vacant": bool(exp.get("vacant")),
                       "view": cls.view if cls else "unknown",
                       "urgent": urgent,
                       "gathering_case": gathering is not None,
                       "at_least": gathering["floor"] if gathering else None,
                       "awareness_only": move_in is not None,
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
    # The area-correlation layer (case #36) sees every offline
    # event: it records this one and, if this event completes an
    # area cluster, annotates the run (cluster_id, and new_cluster
    # when THIS event formed one). Below the threshold it changes
    # nothing about the run.
    _note_offline(engine, run, payload)
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


# ---------------------------------------------------------------------
# Several cameras drop at once: the area correlation (case #36)
# ---------------------------------------------------------------------

# Thresholds, in code where the operator can read them: a cluster
# is 3+ distinct cameras, across 2+ distinct properties, sharing
# one area label, all offline inside a 15-minute window. Anything
# less is just cameras going offline, and the individual offline
# plays speak for themselves.
CLUSTER_MIN_CAMERAS = 3
CLUSTER_MIN_PROPERTIES = 2
CLUSTER_WINDOW_MINUTES = 15
# Months where an outage at an empty home is a freeze risk.
FREEZING_MONTHS = (11, 12, 1, 2, 3)

# In-process record of recent offline events (the staged stand-in
# for the event store a real deployment keeps): one entry per
# (property, camera, minute), each holding the run it produced so
# a cluster can mark its members after the fact.
_OFFLINE_RECORDS: list[dict] = []
# Clusters already notified, by cluster_id: once an area outage
# has been announced, later events in the same footprint (or
# growing it inside the window) join it quietly instead of
# announcing it again.
_NOTIFIED_CLUSTERS: dict[str, dict] = {}


def _reset_outage_correlator() -> None:
    """Test hook: clear the offline-event record and the notified
    clusters between scenarios."""
    _OFFLINE_RECORDS.clear()
    _NOTIFIED_CLUSTERS.clear()


def _abs_minutes(date_str: str, ts: str) -> int | None:
    """Absolute minute count for an event (date ordinal * 1440 +
    minute of day), so windows compare across midnight. None when
    either half cannot be read — an event that cannot be placed
    in time simply does not correlate."""
    minutes = _ts_minutes(ts)
    if minutes is None:
        return None
    try:
        ordinal = _date_cls.fromisoformat(str(date_str).strip()).toordinal()
    except ValueError:
        return None
    return ordinal * 1440 + minutes


def _mark_cluster_run(engine, run: PlayRun, cluster: dict) -> None:
    """Annotate a member run, in its own record: an internal step
    naming the cluster it belongs to. First cluster wins — a run
    already marked is not re-marked. Appending after the run has
    completed mirrors the camera-online resolution step."""
    if any(s.name == "outage_cluster" for s in run.steps):
        return
    step = Step("outage_cluster", "internal")
    step.status = "done"
    step.result = {
        "cluster_id": cluster["cluster_id"],
        "cluster_note": ("this camera's offline event is part of "
                         "an area-outage cluster — the operator "
                         "gets one cluster notification, not a "
                         "separate urgent alert for this camera"),
    }
    run.steps.append(step)
    run.data["cluster_id"] = cluster["cluster_id"]
    engine._log(run, "outage_cluster_marked",
                cluster_id=cluster["cluster_id"])


def _merge_into_cluster(engine, cluster: dict, window: list) -> None:
    """Fold a window's not-yet-clustered records into an announced
    cluster: each joins its members and footprint, and its run is
    marked with the cluster_id. Records already in a cluster stay
    where they are."""
    for r in window:
        if r["cluster_id"]:
            continue
        r["cluster_id"] = cluster["cluster_id"]
        cluster["members"].append({
            "property_id": r["property_id"], "camera": r["camera"],
            "date": r["date"], "ts": r["ts"]})
        cluster["signature"] = cluster["signature"] | {
            (r["property_id"], r["camera"])}
        _mark_cluster_run(engine, r["run"], cluster)


def _public_cluster(cluster: dict) -> dict:
    """The cluster as the sandbox/API may see it: plain data, no
    run references, no signature set."""
    return {
        "cluster_id": cluster["cluster_id"],
        "area": cluster["area"],
        "date": cluster["date"],
        "span_minutes": cluster["span_minutes"],
        "cameras": len({(m["property_id"], m["camera"])
                        for m in cluster["members"]}),
        "properties": sorted({m["property_id"]
                              for m in cluster["members"]}),
        "members": [{"property_id": m["property_id"],
                     "camera": m["camera"], "ts": m["ts"]}
                    for m in cluster["members"]],
    }


def _note_offline(engine, run: PlayRun, payload: dict) -> dict | None:
    """Record one camera_offline event and evaluate the cluster
    rule. Returns the newly formed cluster (also set on the run as
    data["new_cluster"]) or None. Side effects: the run — and, when
    a cluster forms, every member run still on record — is marked
    with the cluster_id. An event whose timestamp cannot be read,
    or whose property carries no area label, is recorded nowhere
    and correlates nothing: correlation needs a time and a place.
    """
    prop = engine.context.get_property(run.property_id)
    area = prop.area if prop is not None else ""
    abs_min = _abs_minutes(run.date, str(payload.get("ts", "")))
    if abs_min is None or not area:
        return None
    camera = str(payload.get("camera", "")).strip()
    for rec in _OFFLINE_RECORDS:
        if (rec["property_id"], rec["camera"], rec["abs_minutes"]) \
                == (run.property_id, camera, abs_min):
            # The same staged event re-fired (a demo re-click):
            # it is already on record. If it already belongs to a
            # cluster, the new run joins it quietly.
            cid = rec.get("cluster_id")
            if cid and cid in _NOTIFIED_CLUSTERS:
                _mark_cluster_run(engine, run, _NOTIFIED_CLUSTERS[cid])
            return None
    record = {"property_id": run.property_id, "camera": camera,
              "area": area, "date": run.date,
              "ts": str(payload.get("ts", "")), "abs_minutes": abs_min,
              "run": run, "cluster_id": ""}
    _OFFLINE_RECORDS.append(record)
    # Bound the record: only the last day can still form a window.
    newest = max(r["abs_minutes"] for r in _OFFLINE_RECORDS)
    _OFFLINE_RECORDS[:] = [r for r in _OFFLINE_RECORDS
                           if r["abs_minutes"] >= newest - 1440]
    window = [r for r in _OFFLINE_RECORDS
              if r["area"] == area
              and abs_min - CLUSTER_WINDOW_MINUTES <= r["abs_minutes"]
              <= abs_min]
    cameras = {(r["property_id"], r["camera"]) for r in window}
    properties = {r["property_id"] for r in window}
    if len(cameras) < CLUSTER_MIN_CAMERAS \
            or len(properties) < CLUSTER_MIN_PROPERTIES:
        return None
    signature = frozenset(cameras)
    # Announced clusters with a member event inside this window: the
    # outage is still the same one, only its footprint is growing.
    live_ids = {r["cluster_id"] for r in window if r["cluster_id"]}
    for cluster in _NOTIFIED_CLUSTERS.values():
        if cluster["area"] != area:
            continue
        if signature <= cluster["signature"]:
            # Already announced, and this event adds no new camera
            # to the footprint: join quietly, mark, do not
            # announce again.
            record["cluster_id"] = cluster["cluster_id"]
            _mark_cluster_run(engine, run, cluster)
            return None
        if cluster["cluster_id"] in live_ids:
            # The announced footprint grew (a camera beyond it went
            # down inside the window): merge the new members into
            # the same cluster, mark them, do not announce again.
            _merge_into_cluster(engine, cluster, window)
            return None
    earliest = min(r["abs_minutes"] for r in window)
    cluster_id = f"outage-{area.lower().replace(' ', '-')}-{earliest}"
    cluster = {
        "cluster_id": cluster_id, "area": area, "date": run.date,
        "span_minutes": abs_min - earliest,
        "signature": signature,
        "members": [{"property_id": r["property_id"],
                     "camera": r["camera"], "date": r["date"],
                     "ts": r["ts"]} for r in window],
    }
    _NOTIFIED_CLUSTERS[cluster_id] = cluster
    for r in window:
        r["cluster_id"] = cluster_id
        _mark_cluster_run(engine, r["run"], cluster)
    run.data["new_cluster"] = cluster
    return cluster


class AreaOutage(Play):
    """Several cameras in one area dropped inside minutes of each
    other. That is not three property problems; it is one area
    event — most likely a power or internet outage — and the
    operator hears about it ONCE, from this play, with the
    affected properties listed. The individual offline runs keep
    their own behavior (a switch message still drafts and holds);
    they are marked as cluster members in their records so nobody
    mistakes them for separate urgent alerts. Constructed per
    cluster by the burst handler; the registered instance carries
    no cluster and holds for a human if ever started bare."""

    name = "camera_outage_cluster"
    description = "Cameras across several properties in one area " \
                  "went offline together: one area-outage " \
                  "notification to the operator."

    def __init__(self, cluster: dict | None = None):
        self.cluster = cluster

    def trigger(self, decision: Decision, message: str, context: dict) -> bool:
        # Event-triggered only: formed by the offline correlation
        # layer, never by a triage Decision.
        return False

    def build(self, run: PlayRun) -> list[Step]:
        return [
            Step("wait_outage_cluster", "wait", event="outage_cluster"),
            Step("assemble_cluster", "internal", run=self._assemble),
            Step("notify_operator", "action", run=self._notify,
                 action_key="notify_operator"),
        ]

    # -- steps ---------------------------------------------------------
    def _assemble(self, engine, run: PlayRun, step: Step) -> None:
        ctx = engine.context
        cluster = self.cluster
        if not cluster:
            step.status = "failed"
            step.result = {"error": "no cluster data on this play"}
            run.hold_reason = "area-outage run started without a cluster"
            return
        members = []
        vacant_names = []
        winter = False
        for m in cluster["members"]:
            prop = ctx.get_property(m["property_id"])
            name = prop.name if prop is not None else m["property_id"]
            vacant = bool(ctx.is_closed(m["property_id"], m["date"])) \
                or (ctx.get_stay(m["property_id"], m["date"]) is None
                    and ctx.get_arrival(m["property_id"], m["date"]) is None)
            try:
                month = _date_cls.fromisoformat(m["date"]).month
            except ValueError:
                month = 0
            if vacant:
                vacant_names.append(name)
                if month in FREEZING_MONTHS:
                    winter = True
            minutes = _ts_minutes(m["ts"])
            members.append({
                "property_id": m["property_id"], "property": name,
                "camera": m["camera"], "date": m["date"], "ts": m["ts"],
                "when": fmt_time(minutes) if minutes is not None
                else m["ts"],
                "vacant": vacant,
            })
        run.data.update({
            "cluster": cluster, "members": members,
            "vacant_properties": vacant_names,
            "winter_freeze_risk": winter,
        })
        step.result = {
            "cluster_id": cluster["cluster_id"], "area": cluster["area"],
            "date": cluster["date"],
            "span_minutes": cluster["span_minutes"],
            "cameras": len({(m["property_id"], m["camera"])
                            for m in members}),
            "properties": sorted({m["property"] for m in members}),
            "vacant_properties": vacant_names,
            "winter_freeze_risk": winter,
        }

    def _notify(self, engine, run: PlayRun, step: Step) -> None:
        cluster = run.data["cluster"]
        members = run.data["members"]
        listing = "; ".join(
            f"{m['property']} ({m['camera']} camera) at {m['when']}"
            for m in members)
        n_cameras = len({(m["property_id"], m["camera"]) for m in members})
        n_props = len({m["property_id"] for m in members})
        summary = (
            f"Likely area power or internet outage — "
            f"{cluster['area']} area, {cluster['date']}: {n_cameras} "
            f"cameras across {n_props} properties went offline "
            f"within {cluster['span_minutes']} minutes: {listing}. "
            f"This is one notification for the whole cluster "
            f"({cluster['cluster_id']}); the individual "
            f"camera-offline runs are marked as part of it instead "
            f"of raising separate urgent alerts."
        )
        if run.data["winter_freeze_risk"]:
            summary += (
                f" Vacant and in a freezing month: "
                f"{', '.join(run.data['vacant_properties'])} — an "
                f"outage at an empty home in winter is a freeze "
                f"risk; the heat may be off with nobody there to "
                f"report it.")
        summary += " Nothing was sent to any guest."
        run.data["outcome"] = "area_outage_notified"
        step.result = {"summary": summary,
                       "cluster_id": cluster["cluster_id"],
                       "area": cluster["area"],
                       "winter_freeze_risk":
                           run.data["winter_freeze_risk"],
                       "vacant_properties": run.data["vacant_properties"],
                       **_effect(step)}


def handle_area_outage_event(engine, cluster: dict) -> PlayRun:
    """Start the area-outage run for a formed cluster and deliver
    its event. The cluster dict comes from the correlation layer
    (see _note_offline / handle_offline_burst)."""
    run = engine.start(AreaOutage(cluster), None,
                       f"[event] outage_cluster {cluster['area']} "
                       f"{cluster['cluster_id']}",
                       "", cluster["date"])
    engine.handle_event(run, "outage_cluster",
                        {"cluster_id": cluster["cluster_id"]})
    return run


def handle_offline_burst(engine, events) -> dict:
    """Ingest a burst of camera_offline events (a storm rolling
    through, a demo button): each event runs the ordinary offline
    play unchanged, the correlation layer evaluates the burst as
    it lands, and every cluster that forms gets its one area-outage
    run (two areas going down in one burst are two outages).
    Returns {"runs": [...], "cluster_run": the first PlayRun |
    None, "cluster": its public cluster dict | None,
    "cluster_runs": [every cluster run], "clusters": [every public
    cluster dict]}."""
    runs = []
    cluster_runs = []
    clusters = []
    for payload in events or []:
        run = handle_camera_offline_event(engine, payload)
        runs.append(run)
        new_cluster = run.data.get("new_cluster")
        if new_cluster is not None and all(
                c["cluster_id"] != new_cluster["cluster_id"]
                for c in clusters):
            cluster_runs.append(handle_area_outage_event(engine, new_cluster))
            clusters.append(_public_cluster(new_cluster))
    return {"runs": runs,
            "cluster_run": cluster_runs[0] if cluster_runs else None,
            "cluster": clusters[0] if clusters else None,
            "cluster_runs": cluster_runs, "clusters": clusters}


# ---------------------------------------------------------------------
# Snow at an empty home: the morning check (final-list case #32)
# ---------------------------------------------------------------------

# The mid-morning deadline, in minutes: snow still down at or
# after 10 AM is a flag; before that the shoveler's window is
# still open and the read is noted, not flagged.
SNOW_DEADLINE_MINUTES = 10 * 60


class MorningCheck(Play):
    """A scheduled mid-morning check at an EMPTY home: is the
    walkway and driveway still snow-covered? Untouched snow
    advertises an empty home, Chicago fines uncleared sidewalks,
    and a cleaner or guest due that day needs a way in — so snow
    still down at mid-morning flags the operator to send the
    shoveler, urgent when someone is due. An occupied home is
    skipped quietly (a stay in progress is not this case's
    business; snow during a stay is a different, already-decided
    case). Clear ground logs. A ground state the frame cannot
    settle notifies — "probably shoveled" is not a read this play
    acts on."""

    name = "camera_morning_check"
    description = "Mid-morning at an empty home: check the frame's " \
                  "ground state for snow still down."

    def __init__(self, vision=None):
        self.vision = vision if vision is not None else FixtureVision()

    def trigger(self, decision: Decision, message: str, context: dict) -> bool:
        # Event-triggered only: the scheduled morning_check event.
        return False

    def build(self, run: PlayRun) -> list[Step]:
        return [
            Step("wait_morning_check", "wait", event="morning_check"),
            Step("match_reality", "internal", run=self._match_reality),
            Step("classify_ground", "internal", run=self._classify_ground),
            Step("log_event", "action", run=self._log_event,
                 action_key="auto_close",
                 skip_if=lambda r: r.data.get("route") != "log"),
            Step("notify_operator", "action", run=self._notify,
                 action_key="notify_operator",
                 skip_if=lambda r: r.data.get("route") != "notify"),
        ]

    # -- helpers -------------------------------------------------------
    def _event(self, run: PlayRun) -> dict:
        return run.data.get("event:morning_check", {})

    # -- steps ---------------------------------------------------------
    def _match_reality(self, engine, run: PlayRun, step: Step) -> None:
        ctx = engine.context
        prop = ctx.get_property(run.property_id)
        if prop is None:
            step.status = "failed"
            step.result = {"error": "no reality data for property"}
            run.hold_reason = "morning check for an unknown property"
            return
        stay = ctx.get_stay(run.property_id, run.date)
        arrival = ctx.get_arrival(run.property_id, run.date)
        turnover = ctx.get_turnover(run.property_id, run.date)
        run.data.update({
            "property_name": prop.name, "stay": stay,
            "arrival": arrival,
            "due_today": arrival is not None or turnover is not None,
        })
        step.result = {
            "property": prop.name,
            "stay_in_progress": stay is not None,
            "guest": stay.guest_name if stay else
                     arrival.guest_name if arrival else "",
            "arrival_today": arrival is not None,
            "turnover_today": turnover is not None,
            "due_today": run.data["due_today"],
        }

    def _classify_ground(self, engine, run: PlayRun, step: Step) -> None:
        event = self._event(run)
        frame_id = str(event.get("frame_id", ""))
        cls = _classify_guarded(self.vision, frame_id) if frame_id \
            else None
        ground = cls.ground if cls else "unknown"
        minutes = _ts_minutes(event.get("ts", ""))
        stay = run.data["stay"]
        if stay is not None:
            route, reason = "log", (
                "a stay is in progress — the snow check is for "
                "empty homes, so an occupied home is skipped "
                "quietly")
        elif cls is None:
            route, reason = "notify", ("the camera frame could not "
                                       "be classified")
        elif ground == "snow_covered":
            if minutes is not None and minutes < SNOW_DEADLINE_MINUTES:
                route, reason = "log", (
                    "snow is down, but it is before the mid-morning "
                    "deadline — noted, not flagged")
            else:
                route, reason = "notify", (
                    "snow still down at an empty home at mid-morning "
                    "— send the shoveler")
        elif ground == "clear":
            route, reason = "log", ("the walkway and driveway read "
                                    "clear")
        else:
            route, reason = "notify", ("the frame could not settle "
                                       "the ground state")
        urgent = bool(run.data["due_today"] and route == "notify")
        run.data.update({
            "classification": cls, "route": route, "ground": ground,
            "outcome": "logged" if route == "log" else "notify_operator",
            "flag_reason": reason, "urgent": urgent,
        })
        step.result = {
            "frame_id": frame_id,
            "camera": str(event.get("camera", "")),
            "scene": cls.scene if cls else "unclassified",
            "ground": ground,
            "note": cls.note if cls else "",
            "urgent": urgent,
            "route": route, "reason": reason,
        }

    def _log_event(self, engine, run: PlayRun, step: Step) -> None:
        step.result = {
            "logged": True, "ground": run.data["ground"],
            "reason": run.data["flag_reason"],
            "note": "logged only — no notification, no message to anyone",
            **_effect(step),
        }

    def _notify(self, engine, run: PlayRun, step: Step) -> None:
        event = self._event(run)
        frame_id = str(event.get("frame_id", ""))
        summary = (
            f"Snow check — {run.data['property_name']}, "
            f"{event.get('camera', 'exterior')} camera, {run.date}: "
            f"the ground reads {run.data['ground']}. Flagged "
            f"because {run.data['flag_reason']}. No stay is in "
            f"progress at this home."
        )
        if run.data["urgent"]:
            summary += (" Urgent: a cleaner or guest is due at "
                        "this home today.")
        summary += " Nothing was sent to anyone."
        if frame_id:
            summary += f" Frame: {frame_id}."
        step.result = {"summary": summary, "ground": run.data["ground"],
                       "reason": run.data["flag_reason"],
                       "urgent": run.data["urgent"],
                       "frame_id": frame_id,
                       **_effect(step)}


def handle_morning_check_event(engine, payload: dict,
                               play: MorningCheck | None = None) -> PlayRun:
    """Entry point: the scheduled morning check fired (event shape:
    property_id, date, kind="morning_check", camera, ts, frame_id).
    Creates the run on the shared rails and delivers the event.
    Malformed events are a caller error and raise; everything after
    that fails safe inside the run."""
    property_id = str(payload.get("property_id", "")).strip()
    date = str(payload.get("date", "")).strip()
    frame_id = str(payload.get("frame_id", "")).strip()
    kind = str(payload.get("kind", "morning_check")).strip()
    if not property_id or not date or not frame_id:
        raise ValueError(
            "morning_check event needs property_id, date and frame_id")
    if kind != "morning_check":
        raise ValueError(f"unknown scheduled-check kind: {kind}")
    run = engine.start(play or MorningCheck(), None,
                       f"[event] morning_check {property_id} {date} "
                       f"{frame_id}",
                       property_id, date)
    engine.handle_event(run, "morning_check", payload)
    return run


# ---------------------------------------------------------------------
# Camera upkeep: the fleet checks itself (final-list case #58)
# ---------------------------------------------------------------------

# Thresholds, in code constants with the evidence always shown:
# the operator judges the note by its counts, not by the play's
# say-so.
UPKEEP_SAME_TIME_TOLERANCE_MINUTES = 30   # "roughly the same time"
UPKEEP_SAME_TIME_MIN_DAYS = 3             # on this many distinct days
UPKEEP_NIGHT_MIN_EVENTS = 20              # enough night motion to judge
UPKEEP_NIGHT_TRIVIAL_SHARE = 0.8          # almost all of it nothing
UPKEEP_UNCLEAR_MIN_FRAMES = 10            # enough frames to judge
UPKEEP_UNCLEAR_SHARE = 0.5                # half or more unclear


def _same_time_offline(events) -> tuple | None:
    """The same-time offline pattern in one camera's history, or
    None: a set of offline events whose times of day sit within
    the tolerance of each other and whose dates span at least
    UPKEEP_SAME_TIME_MIN_DAYS distinct days. Returns (days sorted,
    [(date, minutes), ...]) for the widest such group."""
    points = sorted({(d, to_minutes(t)) for d, t in events})
    best = None
    for _d0, m0 in points:
        group = [(d, m) for d, m in points
                 if abs(m - m0) <= UPKEEP_SAME_TIME_TOLERANCE_MINUTES]
        days = sorted({d for d, _m in group})
        if len(days) >= UPKEEP_SAME_TIME_MIN_DAYS \
                and (best is None or len(days) > len(best[0])):
            best = (days, group)
    return best


def _upkeep_notes(ctx) -> list[dict]:
    """The upkeep report's notes, computed from the fleet's staged
    history. Each note names the turnover task, the camera it
    belongs to, and the evidence counts it rests on — a note with
    no evidence behind it is never produced."""
    notes = []
    history = ctx.camera_history() if hasattr(ctx, "camera_history") \
        else ()
    for rec in history:
        prop = ctx.get_property(rec.property_id)
        name = prop.name if prop is not None else rec.property_id
        same = _same_time_offline(rec.offline_events)
        if same is not None:
            days, group = same
            center = round(sum(m for _d, m in group) / len(group))
            notes.append({
                "property_id": rec.property_id, "property": name,
                "camera": rec.camera, "kind": "wifi",
                "task": f"check the WiFi at {name}",
                "evidence": (
                    f"went offline around {fmt_time(center)} on "
                    f"{len(days)} days "
                    f"({', '.join(days)}) — same time of day, "
                    f"within {UPKEEP_SAME_TIME_TOLERANCE_MINUTES} "
                    f"minutes"),
            })
        lens_evidence = []
        if rec.night_motion >= UPKEEP_NIGHT_MIN_EVENTS \
                and rec.night_motion > 0 \
                and rec.night_trivial / rec.night_motion \
                >= UPKEEP_NIGHT_TRIVIAL_SHARE:
            lens_evidence.append(
                f"{rec.night_trivial} of {rec.night_motion} night "
                f"motion events were nothing or an animal "
                f"({round(100 * rec.night_trivial / rec.night_motion)}%"
                f") — the cobweb signature")
        if rec.frames_total >= UPKEEP_UNCLEAR_MIN_FRAMES \
                and rec.frames_total > 0 \
                and rec.unclear_frames / rec.frames_total \
                >= UPKEEP_UNCLEAR_SHARE:
            lens_evidence.append(
                f"{rec.unclear_frames} of {rec.frames_total} recent "
                f"frames classified unclear "
                f"({round(100 * rec.unclear_frames / rec.frames_total)}%"
                f") — the lens is not seeing clearly")
        if lens_evidence:
            notes.append({
                "property_id": rec.property_id, "property": name,
                "camera": rec.camera, "kind": "lens",
                "task": f"wipe the lens at {name}",
                "evidence": "; ".join(lens_evidence),
            })
    return notes


class CameraUpkeep(Play):
    """A periodic self-check: the camera fleet's own event history
    is read for the two failure shapes that quietly weaken every
    other camera case — a camera that drops offline at the same
    time every day (a WiFi problem on a schedule) and a camera
    whose lens no longer sees clearly (cobwebs firing night motion
    that is never anything, frames classifying unclear). The
    output is an operator-facing report of turnover task notes,
    each tied to its evidence counts. It is a report, never an
    alert storm, and it sends nothing to anyone: no guest, no
    cleaner, ever."""

    name = "camera_upkeep"
    description = "Periodic self-check over the camera fleet's " \
                  "event history: WiFi-pattern and dirty-lens " \
                  "notes for the next turnover list."

    def trigger(self, decision: Decision, message: str, context: dict) -> bool:
        # Event-triggered only: the scheduled camera_upkeep event.
        return False

    def build(self, run: PlayRun) -> list[Step]:
        return [
            Step("wait_camera_upkeep", "wait", event="camera_upkeep"),
            Step("build_report", "internal", run=self._build_report),
            Step("log_event", "action", run=self._log_event,
                 action_key="auto_close",
                 skip_if=lambda r: r.data.get("route") != "log"),
            Step("notify_operator", "action", run=self._notify,
                 action_key="notify_operator",
                 skip_if=lambda r: r.data.get("route") != "report"),
        ]

    # -- steps ---------------------------------------------------------
    def _build_report(self, engine, run: PlayRun, step: Step) -> None:
        ctx = engine.context
        history = ctx.camera_history() \
            if hasattr(ctx, "camera_history") else ()
        notes = _upkeep_notes(ctx)
        # No history on record is not a clean bill of health: the
        # operator is told nothing was checked, never reassured.
        no_history = len(history) == 0
        run.data.update({
            "notes": notes,
            "cameras_checked": len(history),
            "no_history": no_history,
            "route": "report" if notes or no_history else "log",
            "outcome": "upkeep_report" if notes or no_history else "logged",
        })
        step.result = {
            "cameras_checked": len(history),
            "notes": notes,
            "note_count": len(notes),
        }

    def _log_event(self, engine, run: PlayRun, step: Step) -> None:
        step.result = {
            "logged": True, "notes": [],
            "cameras_checked": run.data["cameras_checked"],
            "reason": ("no camera in the fleet's history crosses "
                       "an upkeep threshold — no notes for the "
                       "turnover list"),
            "note": "logged only — no notification, no message to anyone",
            **_effect(step),
        }

    def _notify(self, engine, run: PlayRun, step: Step) -> None:
        notes = run.data["notes"]
        if run.data.get("no_history"):
            summary = (
                f"Camera upkeep — {run.date}: no camera history on "
                f"record — nothing was checked, so this is not a "
                f"clean bill of health. Nothing was sent to anyone."
            )
        else:
            lines = "; ".join(
                f"{n['task']} ({n['camera']} camera): {n['evidence']}"
                for n in notes)
            summary = (
                f"Camera upkeep — {run.date}: {len(notes)} turnover "
                f"task note(s) from the camera fleet's own event "
                f"history ({run.data['cameras_checked']} cameras "
                f"checked). {lines}. These are notes for the next "
                f"turnover list; nothing was sent to anyone."
            )
        step.result = {"summary": summary, "notes": notes,
                       "note_count": len(notes),
                       "cameras_checked": run.data["cameras_checked"],
                       **_effect(step)}


def handle_camera_upkeep_event(engine, payload: dict,
                               play: CameraUpkeep | None = None) -> PlayRun:
    """Entry point: the periodic upkeep check fired (event shape:
    date, kind="camera_upkeep"). The check is fleet-wide, so the
    run carries no single property. Malformed events are a caller
    error and raise."""
    date = str(payload.get("date", "")).strip()
    kind = str(payload.get("kind", "camera_upkeep")).strip()
    if not date:
        raise ValueError("camera_upkeep event needs a date")
    if kind != "camera_upkeep":
        raise ValueError(f"unknown upkeep kind: {kind}")
    run = engine.start(play or CameraUpkeep(), None,
                       f"[event] camera_upkeep {date}", "", date)
    engine.handle_event(run, "camera_upkeep", payload)
    return run


# ---------------------------------------------------------------------
# Vendor proof of service (final-list batch 3, case #23)
# ---------------------------------------------------------------------

def _hhmm(value) -> int:
    """'10:00' -> 600, strictly: a vendor window that cannot be read
    is a caller error, never something to guess at."""
    text = str(value or "").strip()
    try:
        hours, mins = (int(part) for part in text.split(":"))
    except ValueError:
        raise ValueError(f"window time must be HH:MM, got {text!r}") \
            from None
    if not (0 <= hours < 24 and 0 <= mins < 60):
        raise ValueError(f"window time out of range: {text!r}")
    return hours * 60 + mins


def _worker_seen(cls: Classification) -> bool:
    """Presence evidence, and only that, on a frame the caller has
    already found readable: the worker field reads present."""
    return cls.worker == "present"


def _worker_read_contradicted(cls: Classification) -> bool:
    """A "present" worker read on a frame that otherwise shows nobody
    — no person scene and no people estimate — is a model glitch,
    never a sighting: the frame counts as one that could not be
    read."""
    return cls.worker == "present" and not cls.people_count \
        and cls.scene not in _PERSON_SCENES \
        and cls.scene not in ("cleaner", "large_gathering")


def _window_frame_words(frame: dict) -> str:
    """One checked frame, in the no-sighting notification's words:
    the scene read, and for a person who did not read as a worker,
    a pointer to the still — never a guess at who it was."""
    if frame["read"] == "unreadable":
        return "could not be read"
    text = frame["scene"].replace("_", " ")
    if frame["scene"] in _PERSON_SCENES \
            or frame["scene"] in ("large_gathering", "cleaner"):
        text += " — a person who did not read as a worker; check the still"
    return text


class VendorProof(Play):
    """A vendor stated an arrival window. When it closes, the frames
    captured inside it are checked for one thing: whether a person
    who reads as a worker was on camera. A sighting writes a
    proof-of-service log entry carrying the frame reference; no
    sighting notifies the OPERATOR that no vendor was seen inside
    the stated window. The play is anchored to the vendor's own
    window, not a guess at when they should come. It is presence
    evidence and nothing more: it never says who the person was,
    and never says a word about the quality or completion of the
    work. An unread frame is never a sighting, and a window with no
    readable sighting is never logged as proof — the operator is
    told. Nothing is ever sent to the vendor or the guest."""

    name = "camera_vendor_proof"
    description = "A vendor's stated arrival window closes: check the " \
                  "frames inside it for worker presence, log proof of " \
                  "service or tell the operator nobody was seen."

    def __init__(self, vision=None):
        self.vision = vision if vision is not None else FixtureVision()

    def trigger(self, decision: Decision, message: str, context: dict) -> bool:
        # Event-triggered only: the vendor_window event (the vendor's
        # stated arrival window), then the scheduled close.
        return False

    def build(self, run: PlayRun) -> list[Step]:
        return [
            Step("wait_vendor_window", "wait", event="vendor_window"),
            Step("match_reality", "internal", run=self._match_reality),
            Step("wait_window_closed", "wait",
                 event="vendor_window_closed"),
            Step("check_window_frames", "internal",
                 run=self._check_frames),
            Step("log_proof", "action", run=self._log_proof,
                 action_key="auto_close",
                 skip_if=lambda r: r.data.get("route") != "log"),
            Step("notify_operator", "action", run=self._notify,
                 action_key="notify_operator",
                 skip_if=lambda r: r.data.get("route") != "notify"),
        ]

    # -- steps ---------------------------------------------------------
    def _match_reality(self, engine, run: PlayRun, step: Step) -> None:
        event = run.data.get("event:vendor_window", {})
        prop = engine.context.get_property(run.property_id)
        if prop is None:
            step.status = "failed"
            step.result = {"error": "no reality data for property"}
            run.hold_reason = "vendor window for an unknown property"
            return
        start = _hhmm(event.get("window_start"))
        end = _hhmm(event.get("window_end"))
        base = _date_cls.fromisoformat(run.date).toordinal() * 1440
        vendor = str(event.get("vendor", "")).strip() or "the vendor"
        camera = str(event.get("camera", "")).strip()
        window_text = f"{fmt_time(start)}–{fmt_time(end)}"
        run.data.update({
            "property_name": prop.name, "vendor": vendor,
            "camera": camera, "window_text": window_text,
            "window_abs": (base + start, base + end),
        })
        step.result = {
            "property": prop.name, "vendor": vendor,
            "window": window_text, "camera": camera or "all cameras",
            "note": ("anchored to the vendor's own stated arrival "
                     "window; the frames inside it are checked when "
                     "it closes"),
        }

    def _check_frames(self, engine, run: PlayRun, step: Step) -> None:
        close = run.data.get("event:vendor_window_closed", {})
        # The frames the window is checked against: the close event
        # lists them (a live camera system would), else the staged
        # frame store for the property and date.
        listed = close.get("frames")
        if isinstance(listed, list) and listed:
            records = [{"frame_id": str(r.get("frame_id", "")).strip(),
                        "camera": str(r.get("camera", "")).strip(),
                        "ts": str(r.get("ts", "")).strip()}
                       for r in listed if isinstance(r, dict)]
        else:
            records = frames_for(run.property_id, run.date)
        lo, hi = run.data["window_abs"]
        camera = run.data["camera"]
        checked = []
        for rec in records:
            t = _ts_abs_minutes(rec["ts"])
            if not rec["frame_id"] or t is None or not (lo <= t <= hi):
                continue
            if camera and rec["camera"] != camera:
                continue
            cls = _classify_guarded(self.vision, rec["frame_id"])
            if cls is None or cls.scene == "unclear" \
                    or cls.view in _COMPROMISED_VIEWS \
                    or _worker_read_contradicted(cls):
                read = "unreadable"
            elif _worker_seen(cls):
                read = "worker_present"
            else:
                read = "no_worker"
            checked.append({
                "frame_id": rec["frame_id"], "camera": rec["camera"],
                "ts": rec["ts"], "when": fmt_time(t % 1440),
                "scene": cls.scene if cls else "unclassified",
                "read": read,
            })
        checked.sort(key=lambda f: _ts_abs_minutes(f["ts"]))
        sightings = [f for f in checked if f["read"] == "worker_present"]
        unreadable = [f for f in checked if f["read"] == "unreadable"]
        if sightings:
            route, reason = "log", ("worker presence was seen inside "
                                    "the stated window")
        elif not checked:
            route, reason = "notify", ("no camera frames are on record "
                                       "inside the stated window")
        else:
            route, reason = "notify", ("no vendor was seen inside the "
                                       "stated window")
        run.data.update({
            "checked": checked, "sightings": sightings,
            "unreadable": unreadable, "route": route,
            "flag_reason": reason,
            "outcome": "proof_of_service_logged" if route == "log"
            else "notify_operator",
        })
        step.result = {
            "window": run.data["window_text"],
            "vendor": run.data["vendor"],
            "window_frames": checked, "frames_checked": len(checked),
            "sightings": [f["frame_id"] for f in sightings],
            "unreadable": len(unreadable),
            "route": route, "reason": reason,
        }

    def _log_proof(self, engine, run: PlayRun, step: Step) -> None:
        sightings = run.data["sightings"]
        first = sightings[0]
        entry = (
            f"Proof of service (presence only) — "
            f"{run.data['property_name']}, {run.date}: a person "
            f"reading as a worker was on camera at {first['when']} "
            f"(frame {first['frame_id']}, {first['camera']} camera), "
            f"inside the stated arrival window for "
            f"{run.data['vendor']} ({run.data['window_text']})."
        )
        if len(sightings) > 1:
            entry += (" Other sightings inside the window: "
                      + ", ".join(f"{f['frame_id']} ({f['when']})"
                                  for f in sightings[1:]) + ".")
        if run.data["unreadable"]:
            entry += (f" {len(run.data['unreadable'])} other frame(s) "
                      f"inside the window could not be read.")
        entry += (" Presence evidence only: it says nothing about who "
                  "the person was, or about the quality or completion "
                  "of the work.")
        step.result = {
            "logged": True, "entry": entry,
            "frame_id": first["frame_id"],
            "proof_of_service": [{"frame_id": f["frame_id"],
                                  "camera": f["camera"], "ts": f["ts"]}
                                 for f in sightings],
            "reason": run.data["flag_reason"],
            "note": "logged only — no notification, no message to anyone",
            **_effect(step),
        }

    def _notify(self, engine, run: PlayRun, step: Step) -> None:
        checked = run.data["checked"]
        if not checked:
            seen = ("No camera frames are on record inside the window "
                    "— there was nothing to check presence against.")
        else:
            listing = "; ".join(
                f"{f['frame_id']} ({f['camera']}, {f['when']}): "
                f"{_window_frame_words(f)}" for f in checked)
            seen = (f"{len(checked)} frame(s) inside the window were "
                    f"checked and none read as a worker present: "
                    f"{listing}.")
            if run.data["unreadable"]:
                seen += (f" {len(run.data['unreadable'])} of them could "
                         f"not be read, so presence is unsettled on "
                         f"those.")
        summary = (
            f"Vendor proof of service — {run.data['property_name']}, "
            f"{run.date}: no vendor was seen inside the stated window "
            f"({run.data['window_text']}, {run.data['vendor']}). {seen} "
            f"This is the absence of a camera sighting, not proof the "
            f"visit did not happen. Nothing was sent to the vendor or "
            f"the guest."
        )
        step.result = {"summary": summary,
                       "reason": run.data["flag_reason"],
                       "frames_checked": len(checked),
                       "unreadable": len(run.data["unreadable"]),
                       **_effect(step)}


def handle_vendor_window_event(engine, payload: dict,
                               play: VendorProof | None = None) -> PlayRun:
    """Entry point: a vendor stated an arrival window (event shape:
    property_id, date, window_start "HH:MM", window_end "HH:MM",
    optional vendor, camera). Creates the proof-of-service run on
    the shared rails and delivers the window; the run then parks
    until the window closes (handle_vendor_window_closed_event).
    Malformed events — no property, no ISO date, a window that
    cannot be read or does not end after it starts — are a caller
    error and raise; everything after that fails safe inside the
    run."""
    property_id = str(payload.get("property_id", "")).strip()
    date = str(payload.get("date", "")).strip()
    if not property_id or not date:
        raise ValueError("vendor_window event needs property_id, date, "
                         "window_start and window_end")
    try:
        _date_cls.fromisoformat(date)
    except ValueError:
        raise ValueError(f"vendor_window date must be ISO: {date!r}") \
            from None
    start = _hhmm(payload.get("window_start"))
    end = _hhmm(payload.get("window_end"))
    if end <= start:
        raise ValueError("the vendor window must end after it starts")
    run = engine.start(play or VendorProof(), None,
                       f"[event] vendor_window {property_id} {date} "
                       f"{fmt_time(start)}-{fmt_time(end)}",
                       property_id, date)
    engine.handle_event(run, "vendor_window", payload)
    return run


def handle_vendor_window_closed_event(engine, run: PlayRun,
                                      payload: dict | None = None) -> PlayRun:
    """The vendor's stated window closed (the scheduled close). The
    payload may list the frames the camera system captured
    ({"frames": [{"frame_id", "camera", "ts"}, ...]}); otherwise the
    staged frame store is read. Delivers the event; the run checks
    the frames inside the window and logs proof of service or
    notifies the operator."""
    return engine.handle_event(run, "vendor_window_closed", payload or {})
