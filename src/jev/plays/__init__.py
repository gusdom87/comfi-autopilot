"""Jev plays: reusable workflows the triage brain runs on one engine.

Importing this package registers the built plays. Play #1
(early_checkin) is built end to end; the remaining plays from the
scope-v2 plan (guest communications, cleaner-done -> guest-ready,
in-stay issues / handyman dispatch, special requests, review
protection) run on these same rails and are specified in
~/workspace/hackathon/plan-v2-autonomous-ops.md.
"""
from .config import PlaysConfig
from .context import (FixtureContext, LiveTurnoReadAdapter, PropertyInfo,
                      RealityContext, Turnover, default_fixture_data)
from .early_checkin import EarlyCheckIn, extract_requested_minutes
from .engine import STAKES, Play, PlayEngine, PlayRun, Step
from .registry import all_plays, get, match, register

register(EarlyCheckIn())

__all__ = [
    "PlaysConfig", "FixtureContext", "LiveTurnoReadAdapter", "PropertyInfo",
    "RealityContext", "Turnover", "default_fixture_data",
    "EarlyCheckIn", "extract_requested_minutes",
    "STAKES", "Play", "PlayEngine", "PlayRun", "Step",
    "all_plays", "get", "match", "register",
]
