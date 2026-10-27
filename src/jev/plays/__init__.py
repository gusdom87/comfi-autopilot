"""Jev plays: reusable workflows the triage brain runs on one engine.

Importing this package registers the built plays. early_checkin
(Play 2) and cleaner_done -> guest-ready (Play 3) are built end to
end on the shared rails; the remaining plays from the scope-v2 plan
(guest communications, in-stay issues / handyman dispatch, special
requests, review protection) run on these same rails and are
specified in ~/workspace/hackathon/plan-v2-autonomous-ops.md.
"""
from .cleaner_done import CleanerDone, handle_cleaner_done_event
from .config import PlaysConfig
from .context import (Arrival, CleanReport, FixtureContext,
                      LiveTurnoReadAdapter, PropertyInfo, RealityContext,
                      Turnover, default_fixture_data)
from .early_checkin import EarlyCheckIn, extract_requested_minutes
from .engine import STAKES, Play, PlayEngine, PlayRun, Step
from .registry import all_plays, get, match, register

register(EarlyCheckIn())
register(CleanerDone())

__all__ = [
    "PlaysConfig", "Arrival", "CleanReport", "FixtureContext",
    "LiveTurnoReadAdapter", "PropertyInfo", "RealityContext", "Turnover",
    "default_fixture_data",
    "EarlyCheckIn", "extract_requested_minutes",
    "CleanerDone", "handle_cleaner_done_event",
    "STAKES", "Play", "PlayEngine", "PlayRun", "Step",
    "all_plays", "get", "match", "register",
]
