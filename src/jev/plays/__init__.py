"""Jev plays: reusable workflows the triage brain runs on one engine.

Importing this package registers the built plays. early_checkin
(Play 2), cleaner_done -> guest-ready (Play 3), camera_check
(Play 7: an exterior camera classifies a frame against the calendar
and flags the operator), and in_stay_issue (Play 4: a mid-stay
problem gets a reply, a handyman dispatch, and guest updates on the
vendor's events) are built end to end on the shared rails; the
remaining plays from the scope-v2 plan (guest communications is
Play 1, the triage front door itself; special requests and review
protection) run on these same rails and are specified in
~/workspace/hackathon/plan-v2-autonomous-ops.md.
"""
from .camera_check import CameraCheck, handle_camera_event
from .cleaner_done import CleanerDone, handle_cleaner_done_event
from .config import PlaysConfig
from .context import (Arrival, CleanReport, FixtureContext,
                      LiveTurnoReadAdapter, PropertyInfo, RealityContext,
                      Stay, Turnover, Vendor, default_fixture_data)
from .early_checkin import EarlyCheckIn, extract_requested_minutes
from .engine import STAKES, Play, PlayEngine, PlayRun, Step
from .in_stay_issue import InStayIssue, classify_issue
from .registry import all_plays, get, match, register
from .vision import (STAGED_FRAMES, Classification, FixtureVision,
                     LiveVision, parse_classification)

register(EarlyCheckIn())
register(CleanerDone())
register(CameraCheck())
register(InStayIssue())

__all__ = [
    "PlaysConfig", "Arrival", "CleanReport", "FixtureContext",
    "LiveTurnoReadAdapter", "PropertyInfo", "RealityContext", "Stay",
    "Turnover", "Vendor", "default_fixture_data",
    "EarlyCheckIn", "extract_requested_minutes",
    "CleanerDone", "handle_cleaner_done_event",
    "CameraCheck", "handle_camera_event",
    "InStayIssue", "classify_issue",
    "STAGED_FRAMES", "Classification", "FixtureVision", "LiveVision",
    "parse_classification",
    "STAKES", "Play", "PlayEngine", "PlayRun", "Step",
    "all_plays", "get", "match", "register",
]
