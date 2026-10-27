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

    def get_clean_report(self, property_id: str, date: str) -> CleanReport | None:
        """The cleaner's completion record for the property on `date`.
        None means: no clean is on record (a play must fail safe)."""
        ...


class FixtureContext:
    """RealityContext backed by staged data.

    data = {
      "properties": {property_id: {PropertyInfo fields}},
      "turnovers": {"<property_id>|<date>": {Turnover fields}},
      "arrivals": {"<property_id>|<date>": {Arrival fields}},
      "clean_reports": {"<property_id>|<date>": {CleanReport fields}},
    }
    """

    def __init__(self, data: dict):
        self._props = {pid: PropertyInfo(property_id=pid, **kw)
                       for pid, kw in data.get("properties", {}).items()}
        self._turns = {}
        for key, kw in data.get("turnovers", {}).items():
            pid, date = key.split("|", 1)
            self._turns[key] = Turnover(property_id=pid, date=date, **kw)
        self._arrivals = {}
        for key, kw in data.get("arrivals", {}).items():
            pid, date = key.split("|", 1)
            self._arrivals[key] = Arrival(property_id=pid, date=date, **kw)
        self._reports = {}
        for key, kw in data.get("clean_reports", {}).items():
            pid, date = key.split("|", 1)
            self._reports[key] = CleanReport(property_id=pid, date=date, **kw)

    def get_property(self, property_id: str) -> PropertyInfo | None:
        return self._props.get(property_id)

    def get_turnover(self, property_id: str, date: str) -> Turnover | None:
        return self._turns.get(f"{property_id}|{date}")

    def get_arrival(self, property_id: str, date: str) -> Arrival | None:
        return self._arrivals.get(f"{property_id}|{date}")

    def get_clean_report(self, property_id: str, date: str) -> CleanReport | None:
        return self._reports.get(f"{property_id}|{date}")

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

    def get_clean_report(self, property_id: str, date: str):
        self._stub()


def default_fixture_data() -> dict:
    """The staged world the demo runs in. Three Cedar Hollow-style units on
    one arrival day (2026-10-28), one per early check-in outcome:
      cedar-hollow-1n  no turnover            -> grant
      cedar-hollow-2n  turnover ready 2:00 PM -> conditional for a 1 PM ask
      peters-house turnover ready 3:30 PM -> deny for a 1 PM ask
    The same day also carries the cleaner-done fixtures Play 3 runs on:
      cedar-hollow-2n  clean complete, nothing flagged -> guest-ready sends
      peters-house clean complete, problem flagged -> human hold
    Check-in notes are staged instruction lines, never real codes.
    """
    return {
        "properties": {
            "cedar-hollow-1n": {"name": "Owner's Unit 1N, Cedar Hollow",
                            "standard_checkin": "16:00",
                            "typical_clean_minutes": 120,
                            "buffer_minutes": 30, "default_ready": "12:00",
                            "door_note": "The front door keypad takes the "
                                         "code from your check-in message.",
                            "parking_note": "You are welcome to park in the "
                                            "driveway; street parking in "
                                            "front works too."},
            "cedar-hollow-2n": {"name": "Studio 2N, Cedar Hollow",
                            "standard_checkin": "16:00",
                            "typical_clean_minutes": 150,
                            "buffer_minutes": 30, "default_ready": "12:00",
                            "door_note": "The studio door keypad takes the "
                                         "code from your check-in message.",
                            "parking_note": "Please park in the driveway; "
                                            "street parking in front works "
                                            "too."},
            "peters-house": {"name": "Peter's House",
                             "standard_checkin": "16:00",
                             "typical_clean_minutes": 150,
                             "buffer_minutes": 30, "default_ready": "12:00",
                             "door_note": "The front door keypad takes the "
                                          "code from your check-in message.",
                             "parking_note": "You are welcome to park in "
                                             "the driveway."},
        },
        "turnovers": {
            "cedar-hollow-2n|2026-10-28": {
                "checkout_time": "11:00", "cleaner_id": "cleaner-cristina",
                "cleaner_name": "Cristina", "duration_minutes": 150},
            "peters-house|2026-10-28": {
                "checkout_time": "12:30", "cleaner_id": "cleaner-jader",
                "cleaner_name": "Jader", "duration_minutes": 150},
        },
        "arrivals": {
            "cedar-hollow-2n|2026-10-28": {"guest_name": "Dana"},
            "peters-house|2026-10-28": {"guest_name": "Marcus"},
        },
        "clean_reports": {
            "cedar-hollow-2n|2026-10-28": {
                "status": "complete", "finished_at": "1:32 PM",
                "cleaner_id": "cleaner-cristina", "problem_flag": ""},
            "peters-house|2026-10-28": {
                "status": "complete", "finished_at": "3:05 PM",
                "cleaner_id": "cleaner-jader",
                "problem_flag": "Cracked wine glass found in the sink; "
                                "one hand towel stained and set aside."},
        },
    }
