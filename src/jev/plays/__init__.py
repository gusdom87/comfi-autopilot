"""Jev plays: reusable workflows the triage brain runs on one engine.

Importing this package registers the built plays. early_checkin
(Play 2), cleaner_done -> guest-ready (Play 3), and camera_check
(Play 7: an exterior camera classifies a frame against the calendar
and flags the operator) are built end to end on the shared rails;
the remaining plays from the scope-v2 plan (guest communications,
in-stay issues / handyman dispatch, special requests, review
protection) run on these same rails and are specified in
~/workspace/hackathon/plan-v2-autonomous-ops.md.
"""
from .camera_check import CameraCheck, handle_camera_event
from .cleaner_done import CleanerDone, handle_cleaner_done_event
from .config import PlaysConfig
from .context import (Arrival, CleanReport, FixtureContext,
                      LiveTurnoReadAdapter, PropertyInfo, RealityContext,
                      Stay, Turnover, default_fixture_data)
from .early_checkin import EarlyCheckIn, extract_requested_minutes
from .engine import STAKES, Play, PlayEngine, PlayRun, Step
from .registry import all_plays, get, match, register
from .vision import (STAGED_FRAMES, Classification, FixtureVision,
                     LiveVision, parse_classification)

register(EarlyCheckIn())
register(CleanerDone())
register(CameraCheck())

__all__ = [
    "PlaysConfig", "Arrival", "CleanReport", "FixtureContext",
    "LiveTurnoReadAdapter", "PropertyInfo", "RealityContext", "Stay",
    "Turnover", "default_fixture_data",
    "EarlyCheckIn", "extract_requested_minutes",
    "CleanerDone", "handle_cleaner_done_event",
    "CameraCheck", "handle_camera_event",
    "STAGED_FRAMES", "Classification", "FixtureVision", "LiveVision",
    "parse_classification",
    "STAKES", "Play", "PlayEngine", "PlayRun", "Step",
    "all_plays", "get", "match", "register",
]
