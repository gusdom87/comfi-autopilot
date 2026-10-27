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


class FixtureContext:
    """RealityContext backed by staged data.

    data = {
      "properties": {property_id: {PropertyInfo fields}},
      "turnovers": {"<property_id>|<date>": {Turnover fields}},
    }
    """

    def __init__(self, data: dict):
        self._props = {pid: PropertyInfo(property_id=pid, **kw)
                       for pid, kw in data.get("properties", {}).items()}
        self._turns = {}
        for key, kw in data.get("turnovers", {}).items():
            pid, date = key.split("|", 1)
            self._turns[key] = Turnover(property_id=pid, date=date, **kw)

    def get_property(self, property_id: str) -> PropertyInfo | None:
        return self._props.get(property_id)

    def get_turnover(self, property_id: str, date: str) -> Turnover | None:
        return self._turns.get(f"{property_id}|{date}")

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


def default_fixture_data() -> dict:
    """The staged world the demo runs in. Three Cedar Hollow-style units on
    one arrival day (2026-10-28), one per early check-in outcome:
      cedar-hollow-1n  no turnover            -> grant
      cedar-hollow-2n  turnover ready 2:00 PM -> conditional for a 1 PM ask
      peters-house turnover ready 3:30 PM -> deny for a 1 PM ask
    """
    return {
        "properties": {
            "cedar-hollow-1n": {"name": "Owner's Unit 1N, Cedar Hollow",
                            "standard_checkin": "16:00",
                            "typical_clean_minutes": 120,
                            "buffer_minutes": 30, "default_ready": "12:00"},
            "cedar-hollow-2n": {"name": "Studio 2N, Cedar Hollow",
                            "standard_checkin": "16:00",
                            "typical_clean_minutes": 150,
                            "buffer_minutes": 30, "default_ready": "12:00"},
            "peters-house": {"name": "Peter's House",
                             "standard_checkin": "16:00",
                             "typical_clean_minutes": 150,
                             "buffer_minutes": 30, "default_ready": "12:00"},
        },
        "turnovers": {
            "cedar-hollow-2n|2026-10-28": {
                "checkout_time": "11:00", "cleaner_id": "cleaner-cristina",
                "cleaner_name": "Cristina", "duration_minutes": 150},
            "peters-house|2026-10-28": {
                "checkout_time": "12:30", "cleaner_id": "cleaner-jader",
                "cleaner_name": "Jader", "duration_minutes": 150},
        },
    }
