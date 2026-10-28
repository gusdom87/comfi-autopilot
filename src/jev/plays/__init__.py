"""Jev plays: reusable workflows the triage brain runs on one engine.

Importing this package registers the built plays. early_checkin
(Play 2), cleaner_done -> guest-ready (Play 3), camera_check
(Play 7: an exterior camera classifies a frame against the calendar
and flags the operator — with vacant mode, the camera-offline
switch play, and the post-turnover state check as its Tier-1
extensions, and the final-list batch on the same rails: the covered/
turned view route and the stuck-at-the-door dwell pattern inside
the motion play, plus the guest lockout, guest-unsafe, and sunset
dark-entry plays, and in batch 2 the dog-with-no-pet route inside
the motion play, the morning snow check, the area-outage cluster
play the offline correlation layer forms, and the camera upkeep
self-check, and in batch 3 the gathering accumulation and the
move-in-signs pattern inside the motion play, plus the vendor
proof-of-service play), and in_stay_issue (Play 4: a mid-stay problem
gets a reply, a handyman dispatch, and guest updates on the
vendor's events) are built end to end on the shared rails; the
remaining plays from the scope-v2 plan (guest communications is
Play 1, the triage front door itself; special requests and review
protection) run on these same rails and are specified in
~/workspace/hackathon/plan-v2-autonomous-ops.md.
"""
from .camera_check import (CLUSTER_MIN_CAMERAS, CLUSTER_MIN_PROPERTIES,
                           CLUSTER_WINDOW_MINUTES, DOG_REASON,
                           GATHERING_MARGIN_MIN,
                           GATHERING_SUSTAIN_FRAMES,
                           GATHERING_SUSTAIN_SPAN_MINUTES,
                           GATHERING_WINDOW_MINUTES, MENTION_VARIANTS,
                           MOVE_IN_MIN_DAYS, AreaOutage, CameraCheck,
                           CameraOffline, CameraUpkeep, GuestLockout,
                           GuestUnsafe, MorningCheck, PostTurnoverCheck,
                           SunsetCheck, VendorProof, gathering_margin,
                           handle_area_outage_event,
                           handle_camera_event, handle_camera_offline_event,
                           handle_camera_online_event,
                           handle_camera_upkeep_event,
                           handle_guest_lockout_event,
                           handle_guest_unsafe_event,
                           handle_morning_check_event, handle_offline_burst,
                           handle_post_turnover_event,
                           handle_sunset_check_event,
                           handle_vendor_window_closed_event,
                           handle_vendor_window_event, sustained_floor)
from .cleaner_done import CleanerDone, handle_cleaner_done_event
from .config import PlaysConfig
from .context import (Arrival, CameraHistory, CleanReport, FixtureContext,
                      LiveTurnoReadAdapter, PropertyInfo, RealityContext,
                      Stay, Turnover, Vendor, WorkWindow,
                      default_fixture_data)
from .early_checkin import EarlyCheckIn, extract_requested_minutes
from .engine import STAKES, Play, PlayEngine, PlayRun, Step
from .in_stay_issue import InStayIssue, classify_issue
from .registry import all_plays, get, match, register
from .vision import (ANIMAL_KINDS, CARRY_STATES, DOOR_STATES,
                     GROUND_STATES, LIGHT_STATES, VIEW_STATES,
                     WORKER_STATES, STAGED_FRAMES, Classification,
                     FixtureVision, LiveVision, frames_for,
                     parse_classification)

register(EarlyCheckIn())
register(CleanerDone())
register(CameraCheck())
register(CameraOffline())
register(PostTurnoverCheck())
register(GuestLockout())
register(GuestUnsafe())
register(SunsetCheck())
register(MorningCheck())
register(AreaOutage())
register(CameraUpkeep())
register(VendorProof())
register(InStayIssue())

__all__ = [
    "PlaysConfig", "Arrival", "CameraHistory", "CleanReport",
    "FixtureContext", "LiveTurnoReadAdapter", "PropertyInfo",
    "RealityContext", "Stay", "Turnover", "Vendor", "WorkWindow",
    "default_fixture_data",
    "EarlyCheckIn", "extract_requested_minutes",
    "CleanerDone", "handle_cleaner_done_event",
    "CameraCheck", "handle_camera_event",
    "CameraOffline", "handle_camera_offline_event",
    "handle_camera_online_event", "MENTION_VARIANTS",
    "PostTurnoverCheck", "handle_post_turnover_event",
    "GuestLockout", "handle_guest_lockout_event",
    "GuestUnsafe", "handle_guest_unsafe_event",
    "SunsetCheck", "handle_sunset_check_event",
    "MorningCheck", "handle_morning_check_event",
    "AreaOutage", "handle_area_outage_event", "handle_offline_burst",
    "CLUSTER_MIN_CAMERAS", "CLUSTER_MIN_PROPERTIES",
    "CLUSTER_WINDOW_MINUTES", "DOG_REASON",
    "CameraUpkeep", "handle_camera_upkeep_event",
    "GATHERING_WINDOW_MINUTES", "GATHERING_SUSTAIN_FRAMES",
    "GATHERING_SUSTAIN_SPAN_MINUTES", "GATHERING_MARGIN_MIN",
    "gathering_margin", "sustained_floor", "MOVE_IN_MIN_DAYS",
    "VendorProof", "handle_vendor_window_event",
    "handle_vendor_window_closed_event",
    "InStayIssue", "classify_issue",
    "STAGED_FRAMES", "Classification", "FixtureVision", "LiveVision",
    "parse_classification", "frames_for",
    "DOOR_STATES", "LIGHT_STATES", "VIEW_STATES",
    "GROUND_STATES", "ANIMAL_KINDS", "CARRY_STATES", "WORKER_STATES",
    "STAKES", "Play", "PlayEngine", "PlayRun", "Step",
    "all_plays", "get", "match", "register",
]
