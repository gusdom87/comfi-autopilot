"""Reality checks for plays: what is actually true about the property,
the calendar, and the cleaners right now.

Plays never guess at reality. They ask a RealityContext. Two
implementations:

- FixtureContext: staged Turno-style data (properties, same-day
  turnovers, cleaner assignments, typical durations). This is what the
  demo and the tests run on.
- LiveTurnoReadAdapter: STUB, intentionally not wired. Turno's API is
  read-only and the operator's Turno account is worked through the web
  UI, so there is no supported live-read path for the demo. The class
  exists so the wiring point is explicit and labeled, and every method
  raises until a real read path is approved.
"""
from dataclasses import dataclass
from typing import Protocol


def to_minutes(hhmm: str) -> int:
    """'16:00' -> 960. Raises ValueError on garbage; plays fail safe."""
    h, m = hhmm.strip().split(":")
    return int(h) * 60 + int(m)


def to_hhmm(minutes: int) -> str:
    return f"{minutes // 60:02d}:{minutes % 60:02d}"


def fmt_time(minutes: int) -> str:
    """960 -> '4:00 PM' (guest-facing)."""
    h24, m = divmod(minutes, 60)
    suffix = "AM" if h24 < 12 else "PM"
    h12 = h24 % 12 or 12
    return f"{h12}:{m:02d} {suffix}"


@dataclass
class PropertyInfo:
    property_id: str
    name: str
    standard_checkin: str = "16:00"        # "HH:MM", 24h
    typical_clean_minutes: int = 120
    buffer_minutes: int = 30               # settle time after a clean
    default_ready: str = "12:00"           # ready time on no-turnover days
    # Check-in essentials, guest-facing. Fixture wording only: these are
    # staged instruction lines, never a real door code.
    door_note: str = ""
    parking_note: str = ""
    # In-home facts for issue handling (Play 4). Fixture wording only:
    # staged instruction lines that ride along in a dispatch artifact
    # so the handyman knows the hardware - never real codes.
    lock_note: str = ""
    water_note: str = ""
    # Camera oversight (Play 7 extensions). switch_cameras names the
    # exterior cameras known to lose power with the property's light
    # switch; switch_location is the staged, guest-safe instruction
    # line for where that switch is. Fixture wording only.
    switch_cameras: tuple = ()
    switch_location: str = ""
    # Neighborhood label (Play 7 final-list case #36, the area
    # outage correlation): properties sharing an area label are the
    # ones whose cameras dropping together reads as one area event
    # rather than several property events. "" = no area on record;
    # such a property can never join a cluster.
    area: str = ""


@dataclass
class Turnover:
    """A same-day turnover before an arriving guest (Turno-style)."""
    property_id: str
    date: str                              # ISO date, the arrival day
    checkout_time: str                     # "HH:MM" the prior guest leaves
    cleaner_id: str
    cleaner_name: str
    duration_minutes: int | None = None    # None -> property typical

    def expected_finish(self, prop: PropertyInfo) -> int:
        """Minutes since midnight when the unit should be ready."""
        dur = self.duration_minutes if self.duration_minutes is not None \
            else prop.typical_clean_minutes
        return to_minutes(self.checkout_time) + dur + prop.buffer_minutes


@dataclass
class Arrival:
    """A guest arriving at a property on a date (Turno/calendar-style)."""
    property_id: str
    date: str                              # ISO date, the arrival day
    guest_name: str = "there"              # first name, for the greeting
    checkin_time: str = ""                 # "" -> property standard
    guest_count: int = 2                   # booked party size
    # Whether the booking itself lists a pet (Play 7 final-list
    # case #46): True means a dog arriving with this party is
    # exactly what was booked and never flags anyone.
    pet_on_booking: bool = False


@dataclass
class Stay:
    """A stay in progress covering a date: the guest arrived earlier
    and has not left yet (calendar-style). One record per covered
    mid-stay date, the way the fixtures key everything else."""
    property_id: str
    date: str                              # a mid-stay date it covers
    guest_name: str = "there"
    guest_count: int = 2                   # booked party size
    arrival_date: str = ""
    departure_date: str = ""
    pet_on_booking: bool = False           # as on Arrival (case #46)


@dataclass
class Vendor:
    """A vendor on the roster (Play 4). covers lists the issue types
    the vendor takes (empty = all of them); properties lists the units
    covered (empty = all of them). window_note and rates_note are
    fixture language, quoted verbatim in dispatch artifacts - there
    is no real vendor and there are no real rates behind them in
    this build."""
    vendor_id: str
    name: str
    covers: tuple = ()
    properties: tuple = ()
    window_note: str = ""
    rates_note: str = ""


@dataclass
class WorkWindow:
    """A scheduled worker window on a property/date: a cleaner visit
    or a vendor call staged in the fixtures. Camera reality checks
    use these windows to tell an expected worker from an unexpected
    person, including at a closed/vacant property."""
    property_id: str
    date: str                              # ISO date the window is on
    label: str = "Scheduled worker"
    start: str = "00:00"                   # "HH:MM"
    end: str = "23:59"                     # "HH:MM"
    kind: str = "vendor"                   # cleaner | vendor | service


@dataclass
class CameraHistory:
    """One camera's own event history (Play 7 final-list case #58,
    camera upkeep): the staged stand-in for what a real camera fleet
    accumulates — when this camera went offline (one (date, "HH:MM")
    pair per event), and counters over its recent frames. The
    upkeep check reads these for patterns; the counters are the
    evidence its notes cite, never a live feed.
    """
    property_id: str
    camera: str
    offline_events: tuple = ()             # of (date, "HH:MM")
    night_motion: int = 0                  # motion events at night
    night_trivial: int = 0                 # of those, nothing/animal
    unclear_frames: int = 0                # frames classified unclear
    frames_total: int = 0                  # frames in the window


@dataclass
class CleanReport:
    """The cleaner's completion record for a property on a date, as the
    booking system of record shows it (Turno-style). A cleaner-done
    event claims a finish; this record is what a play verifies against.
    problem_flag carries anything the cleaner noted while cleaning -
    damage, a missing item, a maintenance issue - in their own words."""
    property_id: str
    date: str                              # ISO date the clean belongs to
    status: str = "complete"               # complete | in_progress
    finished_at: str = ""                  # "1:32 PM", display form
    cleaner_id: str = ""
    problem_flag: str = ""                 # "" -> nothing flagged


class RealityContext(Protocol):
    """The questions a play may ask about the real world."""

    def get_property(self, property_id: str) -> PropertyInfo | None:
        ...

    def get_turnover(self, property_id: str, date: str) -> Turnover | None:
        """The same-day turnover before a guest arriving on `date`, if any."""
        ...

    def ready_time_minutes(self, property_id: str, date: str) -> int | None:
        """When the unit will be ready for a guest arriving on `date`.
        None means: we do not know (a play must fail safe on None)."""
        ...

    def get_arrival(self, property_id: str, date: str) -> Arrival | None:
        """The guest arriving at the property on `date`, if there is one."""
        ...

    def get_stay(self, property_id: str, date: str) -> Stay | None:
        """The stay in progress covering `date` (guest arrived earlier,
        still in residence), if there is one."""
        ...

    def get_clean_report(self, property_id: str, date: str) -> CleanReport | None:
        """The cleaner's completion record for the property on `date`.
        None means: no clean is on record (a play must fail safe)."""
        ...

    def get_vendor(self, issue_type: str, property_id: str) -> Vendor | None:
        """The roster vendor who covers this issue type at this
        property, if there is one. None means: nobody on the roster
        takes it (a play must fail safe)."""
        ...

    def is_closed(self, property_id: str, date: str) -> bool:
        """True when the property is marked vacant/closed on `date`:
        no guest is expected and any person needs an explanation."""
        ...

    def get_work_windows(self, property_id: str, date: str) -> tuple:
        """Scheduled cleaner/vendor windows for the property on
        `date`, beyond the same-day turnover record, if any."""
        ...

    def dark_nights(self, property_id: str) -> int:
        """How many consecutive arrival-day sunset checks in a row
        have found this property's entry dark (Play 7 case #11).
        Zero means the last counted check found the lights on, or
        no dark night has been counted yet."""
        ...

    def note_dark_night(self, property_id: str, dark: bool) -> int:
        """Record one sunset check's entry-lights read for the
        property: dark extends the consecutive-dark streak by one,
        lit resets it to zero. Returns the streak after this read."""
        ...

    def camera_history(self) -> tuple:
        """Every camera's staged event history (CameraHistory
        records), for the upkeep self-check. An empty tuple means
        no fleet history is on record — the check must say so
        rather than invent a clean bill of health."""
        ...


class FixtureContext:
    """RealityContext backed by staged data.

    data = {
      "properties": {property_id: {PropertyInfo fields}},
      "turnovers": {"<property_id>|<date>": {Turnover fields}},
      "arrivals": {"<property_id>|<date>": {Arrival fields}},
      "stays": {"<property_id>|<date>": {Stay fields}},
      "clean_reports": {"<property_id>|<date>": {CleanReport fields}},
      "vendors": {vendor_id: {Vendor fields}},
      "closed_dates": ["<property_id>|<date>", ...],
      "work_windows": {"<property_id>|<date>": [{WorkWindow fields}]},
      "dark_nights": {property_id: consecutive dark entry nights},
      "camera_history": [{CameraHistory fields, with offline_events
        as [{"date": ..., "time": "HH:MM"}, ...]}],
    }
    """

    def __init__(self, data: dict):
        self._props = {}
        for pid, kw in data.get("properties", {}).items():
            kw = dict(kw)
            if "switch_cameras" in kw:
                kw["switch_cameras"] = tuple(kw["switch_cameras"])
            self._props[pid] = PropertyInfo(property_id=pid, **kw)
        self._turns = {}
        for key, kw in data.get("turnovers", {}).items():
            pid, date = key.split("|", 1)
            self._turns[key] = Turnover(property_id=pid, date=date, **kw)
        self._arrivals = {}
        for key, kw in data.get("arrivals", {}).items():
            pid, date = key.split("|", 1)
            self._arrivals[key] = Arrival(property_id=pid, date=date, **kw)
        self._stays = {}
        for key, kw in data.get("stays", {}).items():
            pid, date = key.split("|", 1)
            self._stays[key] = Stay(property_id=pid, date=date, **kw)
        self._reports = {}
        for key, kw in data.get("clean_reports", {}).items():
            pid, date = key.split("|", 1)
            self._reports[key] = CleanReport(property_id=pid, date=date, **kw)
        self._vendors = {}
        for vid, kw in data.get("vendors", {}).items():
            kw = dict(kw)
            if "covers" in kw:
                kw["covers"] = tuple(kw["covers"])
            if "properties" in kw:
                kw["properties"] = tuple(kw["properties"])
            self._vendors[vid] = Vendor(vendor_id=vid, **kw)
        closed = data.get("closed_dates", [])
        if isinstance(closed, dict):
            closed = list(closed.keys())
        self._closed = set(closed)
        self._work_windows = {}
        for key, windows in data.get("work_windows", {}).items():
            pid, date = key.split("|", 1)
            self._work_windows[key] = tuple(
                WorkWindow(property_id=pid, date=date, **kw)
                for kw in windows)
        self._dark_nights = {str(pid): int(n) for pid, n
                             in data.get("dark_nights", {}).items()}
        self._camera_history = []
        for kw in data.get("camera_history", []):
            kw = dict(kw)
            events = tuple((str(e["date"]), str(e["time"]))
                           for e in kw.pop("offline_events", []))
            self._camera_history.append(
                CameraHistory(offline_events=events, **kw))
        self._camera_history = tuple(self._camera_history)

    def get_property(self, property_id: str) -> PropertyInfo | None:
        return self._props.get(property_id)

    def get_turnover(self, property_id: str, date: str) -> Turnover | None:
        return self._turns.get(f"{property_id}|{date}")

    def get_arrival(self, property_id: str, date: str) -> Arrival | None:
        return self._arrivals.get(f"{property_id}|{date}")

    def get_stay(self, property_id: str, date: str) -> Stay | None:
        return self._stays.get(f"{property_id}|{date}")

    def get_clean_report(self, property_id: str, date: str) -> CleanReport | None:
        return self._reports.get(f"{property_id}|{date}")

    def get_vendor(self, issue_type: str, property_id: str) -> Vendor | None:
        for vendor in self._vendors.values():
            if vendor.covers and issue_type not in vendor.covers \
                    and "*" not in vendor.covers:
                continue
            if vendor.properties and property_id not in vendor.properties:
                continue
            return vendor
        return None

    def is_closed(self, property_id: str, date: str) -> bool:
        return f"{property_id}|{date}" in self._closed

    def get_work_windows(self, property_id: str, date: str) -> tuple:
        return self._work_windows.get(f"{property_id}|{date}", ())

    def dark_nights(self, property_id: str) -> int:
        return self._dark_nights.get(property_id, 0)

    def note_dark_night(self, property_id: str, dark: bool) -> int:
        streak = self._dark_nights.get(property_id, 0) + 1 if dark else 0
        self._dark_nights[property_id] = streak
        return streak

    def camera_history(self) -> tuple:
        return self._camera_history

    def ready_time_minutes(self, property_id: str, date: str) -> int | None:
        prop = self.get_property(property_id)
        if prop is None:
            return None
        turn = self.get_turnover(property_id, date)
        if turn is None:
            return to_minutes(prop.default_ready)
        return turn.expected_finish(prop)


class LiveTurnoReadAdapter:
    """STUB — NOT WIRED, DO NOT USE IN THE DEMO.

    Turno's API is read-only and the operator's account access is via
    the web UI; there is no approved live-read path yet. This adapter
    marks exactly where live reads would plug in once one exists. Every
    method raises so nothing can silently pretend to have live data.
    """

    def _stub(self):
        raise NotImplementedError(
            "LiveTurnoReadAdapter is a stub: Turno live reads are not "
            "wired (read-only API, web-UI account). Use FixtureContext."
        )

    def get_property(self, property_id: str):
        self._stub()

    def get_turnover(self, property_id: str, date: str):
        self._stub()

    def ready_time_minutes(self, property_id: str, date: str):
        self._stub()

    def get_arrival(self, property_id: str, date: str):
        self._stub()

    def get_stay(self, property_id: str, date: str):
        self._stub()

    def get_clean_report(self, property_id: str, date: str):
        self._stub()

    def get_vendor(self, issue_type: str, property_id: str):
        self._stub()

    def is_closed(self, property_id: str, date: str):
        self._stub()

    def get_work_windows(self, property_id: str, date: str):
        self._stub()

    def dark_nights(self, property_id: str):
        self._stub()

    def note_dark_night(self, property_id: str, dark: bool):
        self._stub()

    def camera_history(self):
        self._stub()


def default_fixture_data() -> dict:
    """The staged world the demo runs in. Three Cedar Hollow-style units
    on one arrival day (2026-10-28), one per early check-in outcome:
      cedar-1a    no turnover            -> grant
      cedar-2a    turnover ready 2:00 PM -> conditional for a 1 PM ask
      maple-house turnover ready 3:30 PM -> deny for a 1 PM ask
    The same day also carries the cleaner-done fixtures Play 3 runs on:
      cedar-2a    clean complete, nothing flagged -> guest-ready sends
      maple-house clean complete, problem flagged -> human hold
    And one stay in progress for Play 7 (camera check): Alex's party
    of four is mid-stay at cedar-1a on 2026-10-29, which is what
    the staged large-gathering frame (vision.STAGED_FRAMES f3) is
    checked against. The frames themselves live in plays/vision.py.
    The same stay is Play 4's (in-stay issues) reality: Alex is in
    residence at cedar-1a on 2026-10-29, and the vendor roster
    carries one fixture handyman covering every property and issue.
    Maple Street House is additionally marked closed/vacant on
    2026-10-30, with one staged service window that morning, for Play
    7's vacant mode. The Cedar Hollow units' front-door cameras are staged as
    switch-fed, with guest-safe switch-location lines, for the
    camera-offline extension. Check-in notes are staged instruction
    lines, never real codes.
    The final-list camera batch adds: Sam's one-night weekend stay
    at cedar-1a on 2026-10-30 (a Friday), the covered-camera
    frame's stay reality (vision.STAGED_FRAMES f10); and a staged
    dark-entry streak — Studio 2A carries one consecutive dark
    night already, so its 2026-10-28 sunset check (frame f16) is
    the second night running, which is what puts a bulb change on
    the next turnover list (case #11).
    Final-list batch 2 adds: an area label ("Cedar Hollow") on all
    three properties, so cameras dropping together correlate into
    one area outage (case #36); and a staged camera history for
    the upkeep check (case #58) — Garden Unit 1A's front-door
    camera drops offline around 7 AM three days running (the WiFi
    pattern), Studio 2A's front-door camera fires night motion
    that is almost all nothing-or-animal (the cobweb signature),
    and Maple Street House's driveway camera classifies most of
    its frames unclear (a dirty lens). 1A's backyard camera is staged
    below every upkeep threshold, as the control.
    Final-list batch 3 adds one long stay for the move-in-signs case
    (#55): Jordan, a party of one, arrives at Studio 2A on 2026-11-02
    for most of November. The stay is keyed on the dates its frames
    use (the arrival record on 11-02, stay records 11-03 to 11-05),
    all in November so no earlier fixture date changes meaning. The
    gathering accumulation (#6) and the vendor proof check (#23)
    reuse Alex's stay and Maple Street House's closed date with its
    service window; they need frames, not new calendar records.
    """
    return {
        "properties": {
            "cedar-1a": {"name": "Garden Unit 1A, Cedar Hollow",
                         "standard_checkin": "16:00",
                         "typical_clean_minutes": 120,
                         "buffer_minutes": 30, "default_ready": "12:00",
                         "door_note": "The front door keypad takes the "
                                      "code from your check-in message.",
                         "parking_note": "You are welcome to park in the "
                                         "driveway; street parking in "
                                         "front works too.",
                         "lock_note": "Front door is a keypad deadbolt; "
                                      "the battery pack sits on the "
                                      "inside face of the door.",
                         "water_note": "Water shutoff is in the basement "
                                       "utility room, on the wall by "
                                       "the water heater.",
                         "switch_cameras": ["front-door"],
                         "switch_location": "by the front door, inside",
                         "area": "Cedar Hollow"},
            "cedar-2a": {"name": "Studio 2A, Cedar Hollow",
                         "standard_checkin": "16:00",
                         "typical_clean_minutes": 150,
                         "buffer_minutes": 30, "default_ready": "12:00",
                         "door_note": "The studio door keypad takes the "
                                      "code from your check-in message.",
                         "parking_note": "Please park in the driveway; "
                                         "street parking in front works "
                                         "too.",
                         "lock_note": "Studio door is a keypad deadbolt.",
                         "water_note": "Water shutoff is under the "
                                       "kitchen sink cabinet.",
                         "switch_cameras": ["front-door"],
                         "switch_location": "by the studio front door, "
                                            "inside",
                         "area": "Cedar Hollow"},
            "maple-house": {"name": "Maple Street House",
                            "standard_checkin": "16:00",
                            "typical_clean_minutes": 150,
                            "buffer_minutes": 30, "default_ready": "12:00",
                            "door_note": "The front door keypad takes the "
                                         "code from your check-in message.",
                            "parking_note": "You are welcome to park in "
                                            "the driveway.",
                            "lock_note": "Front door is a keypad deadbolt "
                                         "over a lever handle.",
                            "water_note": "Water shutoff is in the "
                                          "first-floor utility closet.",
                            "area": "Cedar Hollow"},
        },
        "turnovers": {
            "cedar-2a|2026-10-28": {
                "checkout_time": "11:00", "cleaner_id": "cleaner-lena",
                "cleaner_name": "Lena", "duration_minutes": 150},
            "maple-house|2026-10-28": {
                "checkout_time": "12:30", "cleaner_id": "cleaner-tomas",
                "cleaner_name": "Tomas", "duration_minutes": 150},
        },
        "arrivals": {
            "cedar-2a|2026-10-28": {"guest_name": "Dana"},
            "maple-house|2026-10-28": {"guest_name": "Marcus"},
            "cedar-2a|2026-11-02": {"guest_name": "Jordan",
                                    "guest_count": 1},
        },
        "stays": {
            "cedar-1a|2026-10-29": {
                "guest_name": "Alex", "guest_count": 4,
                "arrival_date": "2026-10-27",
                "departure_date": "2026-10-30"},
            "cedar-1a|2026-10-30": {
                "guest_name": "Sam", "guest_count": 2,
                "arrival_date": "2026-10-30",
                "departure_date": "2026-10-31"},
            "cedar-2a|2026-11-03": {
                "guest_name": "Jordan", "guest_count": 1,
                "arrival_date": "2026-11-02",
                "departure_date": "2026-11-28"},
            "cedar-2a|2026-11-04": {
                "guest_name": "Jordan", "guest_count": 1,
                "arrival_date": "2026-11-02",
                "departure_date": "2026-11-28"},
            "cedar-2a|2026-11-05": {
                "guest_name": "Jordan", "guest_count": 1,
                "arrival_date": "2026-11-02",
                "departure_date": "2026-11-28"},
        },
        "clean_reports": {
            "cedar-2a|2026-10-28": {
                "status": "complete", "finished_at": "1:32 PM",
                "cleaner_id": "cleaner-lena", "problem_flag": ""},
            "maple-house|2026-10-28": {
                "status": "complete", "finished_at": "3:05 PM",
                "cleaner_id": "cleaner-tomas",
                "problem_flag": "Cracked wine glass found in the sink; "
                                "one hand towel stained and set aside."},
        },
        "closed_dates": ["maple-house|2026-10-30"],
        "dark_nights": {"cedar-2a": 1},
        "camera_history": [
            {"property_id": "cedar-1a", "camera": "front-door",
             "offline_events": [{"date": "2026-10-25", "time": "07:03"},
                                {"date": "2026-10-26", "time": "07:08"},
                                {"date": "2026-10-27", "time": "07:05"}],
             "night_motion": 8, "night_trivial": 3,
             "unclear_frames": 2, "frames_total": 45},
            {"property_id": "cedar-1a", "camera": "backyard",
             "offline_events": [{"date": "2026-10-26", "time": "22:40"}],
             "night_motion": 5, "night_trivial": 2,
             "unclear_frames": 1, "frames_total": 30},
            {"property_id": "cedar-2a", "camera": "front-door",
             "offline_events": [],
             "night_motion": 46, "night_trivial": 41,
             "unclear_frames": 3, "frames_total": 52},
            {"property_id": "maple-house", "camera": "driveway",
             "offline_events": [{"date": "2026-10-26", "time": "23:58"}],
             "night_motion": 12, "night_trivial": 4,
             "unclear_frames": 18, "frames_total": 30},
        ],
        "work_windows": {
            "maple-house|2026-10-30": [{
                "label": "Fixture service visit", "start": "10:00",
                "end": "12:00", "kind": "vendor"}],
        },
        "vendors": {
            "vendor-fixture-handyman": {
                "name": "Fixture Handyman", "covers": [], "properties": [],
                "window_note": "Same-day window 8 AM-8 PM; typical arrival "
                               "inside 2 hours of dispatch.",
                "rates_note": "Fixture rate: $95 service call, billed to "
                              "the operator, never to the guest."},
        },
    }
