"""Camera-frame vision for Play 7 (camera check).

Two implementations behind one small interface, classify(frame_id)
-> Classification | None:

- FixtureVision: staged classifications keyed by frame_id, from
  STAGED_FRAMES below. Metadata-only frame records: no real photos,
  no real people. This is what the demo and the tests run on. Zero
  live calls.
- LiveVision: sends a frame image to muse-spark-1.3 through the Meta
  Model API — VERIFIED live 2026-10-27 (see the class docstring for
  the confirmed Responses-native image part shape). The sandbox and
  the tests still run on the staged fixture path; live is used only
  when explicitly wired with an API key and real frame bytes.

A classification is strict JSON: {"scene": one of SCENES,
"people_count": int | null, "note": short string}. It may also carry
the optional post-turnover state fields "door" (open | closed |
unknown) and "lights" (on | off | unknown), and the optional
camera-health field "view" (normal | blocked | shifted | unknown);
when present they are validated just as strictly, and when absent
they default to "unknown". Anything that does not parse to exactly
that shape returns None, and callers treat None exactly like an
"unclear" frame: fail safe to a human.

Batch 2 of the final list adds two more optional fields on the same
terms: "ground" (clear | snow_covered | unknown) for the snow check
at an empty home, and "animal" (dog_sized | small | unknown) — how
big the animal in the frame is, which is what the dog-with-no-pet
route stakes its one notification on. Absent still means "unknown",
and "unknown" never fires either route.

Batch 3 adds two more on the same terms: "carrying"
(boxes_or_belongings | luggage | none | unknown) — what the people
in the frame are carrying, which the move-in-signs pattern counts
only when it reads boxes_or_belongings (ordinary travel luggage
never counts) — and "worker" (present | absent | unknown) — whether
a person in the frame reads as a worker on a job, which is the only
presence evidence the vendor proof-of-service check accepts. Absent
means "unknown", and "unknown" is never evidence of anything.
"""
import base64
import json
import urllib.error
import urllib.request
from dataclasses import dataclass

from ..config import Config
from ..parse import extract_text, usage_cost

SCENES = (
    "expected_guest",    # people arriving the way a checking-in guest does
    "cleaner",           # a cleaner arriving / working
    "unknown_person",    # a person, matching nobody expected
    "large_gathering",   # a crowd, more than a normal arrival
    "vehicle",           # a car, no people of note
    "animal",            # an animal triggered the motion
    "nothing_notable",   # motion, but nothing that matters
    "unclear",           # too dark / blurry / obstructed to judge
)

DOOR_STATES = ("open", "closed", "unknown")
LIGHT_STATES = ("on", "off", "unknown")
VIEW_STATES = ("normal", "blocked", "shifted", "unknown")
GROUND_STATES = ("clear", "snow_covered", "unknown")
ANIMAL_KINDS = ("dog_sized", "small", "unknown")
CARRY_STATES = ("boxes_or_belongings", "luggage", "none", "unknown")
WORKER_STATES = ("present", "absent", "unknown")

VISION_SYSTEM = (
    "You classify a single still frame from an exterior camera at a "
    "short-term rental property. Reply with ONLY a JSON object: "
    '{"scene": one of ["expected_guest", "cleaner", "unknown_person", '
    '"large_gathering", "vehicle", "animal", "nothing_notable", '
    '"unclear"], "people_count": integer or null, "note": one short '
    'sentence, "door": one of ["open", "closed", "unknown"], '
    '"lights": one of ["on", "off", "unknown"], '
    '"view": one of ["normal", "blocked", "shifted", "unknown"], '
    '"ground": one of ["clear", "snow_covered", "unknown"], '
    '"animal": one of ["dog_sized", "small", "unknown"], '
    '"carrying": one of ["boxes_or_belongings", "luggage", "none", '
    '"unknown"], "worker": one of ["present", "absent", "unknown"]}. '
    "You cannot identify who a person is — judge only the kind of "
    "scene. Judge the door and lights state only when a door or the "
    'home\'s lights are actually visible; otherwise report "unknown" '
    "for that field. Judge the view state from the frame itself: "
    '"blocked" when the lens appears covered or obstructed (tape, '
    'cloth, an object over it), "shifted" when the frame looks as '
    "if the camera has been turned or knocked (pointing at the "
    'ground, the sky, or a wall instead of the usual scene), '
    '"normal" when neither is the case, and "unknown" when the '
    "frame does not let you judge. Judge the ground only when the "
    'walkway or driveway is actually visible: "snow_covered" when '
    'snow covers it, "clear" when it is bare, "unknown" otherwise. '
    'Judge the animal field only when an animal is visible: '
    '"dog_sized" for an animal the size of a dog (a dog, in '
    'practice), "small" for anything smaller (a cat, a raccoon), '
    '"unknown" when there is no animal or its size cannot be '
    "judged. Judge the carrying field only when a person is "
    'visible: "boxes_or_belongings" for moving boxes, bins, '
    "furniture, a mattress, or household belongings beyond travel "
    'luggage; "luggage" for ordinary travel bags, suitcases, or '
    'groceries; "none" when the people carry nothing; "unknown" '
    "when there is no person or it cannot be judged. Judge the "
    'worker field from the frame itself: "present" when a person '
    "in the frame looks like a worker on a job (tools, work "
    'supplies, work clothes, a work vehicle beside them), "absent" '
    "when the frame clearly shows no such person, and \"unknown\" "
    "when it cannot be judged. If the frame is too dark, blurry, "
    'or obstructed to judge, the scene is "unclear".'
)


@dataclass
class Classification:
    scene: str                      # one of SCENES
    people_count: int | None
    note: str
    source: str = "fixture"         # fixture | live
    cost_usd: float = 0.0
    # Post-turnover state (Play 7 extension): what the frame shows
    # about the door and the lights. "unknown" unless the frame (or
    # the staged record) actually settles it.
    door: str = "unknown"           # one of DOOR_STATES
    lights: str = "unknown"         # one of LIGHT_STATES
    # Camera health (Play 7 final-list case #17): whether the frame
    # shows the camera itself compromised — the lens blocked, or the
    # view shifted from where the camera points. "unknown" unless
    # the frame (or the staged record) actually settles it; a merely
    # unclear frame never counts as blocked or shifted.
    view: str = "unknown"           # one of VIEW_STATES
    # Ground state (final-list case #32, snow at an empty home):
    # whether the walkway/driveway in the frame reads clear or
    # still snow-covered. "unknown" unless the frame settles it.
    ground: str = "unknown"         # one of GROUND_STATES
    # Animal size (final-list case #46, dog with no pet on the
    # booking): how big the animal in the frame is. Only
    # "dog_sized" can ever fire the dog route; a cat or a raccoon
    # is "small" and never does. "unknown" unless settled.
    animal: str = "unknown"         # one of ANIMAL_KINDS
    # What the people in the frame carry (batch 3, case #55, move-in
    # signs): only "boxes_or_belongings" counts toward the pattern;
    # ordinary luggage never does. "unknown" unless settled.
    carrying: str = "unknown"       # one of CARRY_STATES
    # Whether a person in the frame reads as a worker on a job
    # (batch 3, case #23, vendor proof of service): the presence
    # evidence the proof check accepts. A read, never an identity.
    worker: str = "unknown"         # one of WORKER_STATES


def parse_classification(text: str, source: str = "live",
                         cost_usd: float = 0.0) -> Classification | None:
    """Strict parse of a classification reply. Anything unexpected
    returns None (the caller fails safe), mirroring parse_decision."""
    if not text:
        return None
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end <= start:
        return None
    try:
        obj = json.loads(text[start:end + 1])
        scene = obj["scene"]
        if scene not in SCENES:
            return None
        people = obj.get("people_count")
        if people is not None:
            if isinstance(people, bool) or not isinstance(people, int) \
                    or people < 0:
                return None
        door = obj.get("door", "unknown")
        lights = obj.get("lights", "unknown")
        if door not in DOOR_STATES or lights not in LIGHT_STATES:
            return None
        view = obj.get("view", "unknown")
        if view not in VIEW_STATES:
            return None
        ground = obj.get("ground", "unknown")
        animal = obj.get("animal", "unknown")
        if ground not in GROUND_STATES or animal not in ANIMAL_KINDS:
            return None
        carrying = obj.get("carrying", "unknown")
        worker = obj.get("worker", "unknown")
        if carrying not in CARRY_STATES or worker not in WORKER_STATES:
            return None
        return Classification(scene=scene, people_count=people,
                              note=str(obj.get("note", "")),
                              source=source, cost_usd=cost_usd,
                              door=door, lights=lights, view=view,
                              ground=ground, animal=animal,
                              carrying=carrying, worker=worker)
    except (ValueError, KeyError, TypeError):
        return None


# The staged frame world Play 7 runs on in the demo and the tests.
# Metadata only: what the frame depicts, in words, plus the staged
# classification FixtureVision returns for it. Wired to the fixture
# calendar in context.default_fixture_data (same properties/dates).
STAGED_FRAMES: dict[str, dict] = {
    "f1": {
        "property_id": "cedar-2a", "date": "2026-10-28",
        "camera": "front-door", "ts": "2026-10-28T15:42:00",
        "description": "Two adults with rolling suitcases at the "
                       "front door, mid-afternoon on arrival day.",
        "classification": {
            "scene": "expected_guest", "people_count": 2,
            "note": "Two people with suitcases at the front door."},
    },
    "f2": {
        "property_id": "cedar-2a", "date": "2026-10-28",
        "camera": "front-door", "ts": "2026-10-28T11:06:00",
        "description": "One person carrying a caddy of cleaning "
                       "supplies, entering just after checkout.",
        "classification": {
            "scene": "cleaner", "people_count": 1,
            "note": "One person with cleaning supplies entering."},
    },
    "f3": {
        "property_id": "cedar-1a", "date": "2026-10-29",
        "camera": "backyard", "ts": "2026-10-29T21:48:00",
        "description": "A large group gathered on the patio in the "
                       "evening, mid-stay, well beyond the booked "
                       "party of four.",
        "classification": {
            "scene": "large_gathering", "people_count": 11,
            "note": "About eleven people gathered on the patio."},
    },
    "f4": {
        "property_id": "maple-house", "date": "2026-10-28",
        "camera": "driveway", "ts": "2026-10-28T23:58:00",
        "description": "Near-black frame just before midnight; motion "
                       "triggered but nothing is identifiable.",
        "classification": {
            "scene": "unclear", "people_count": None,
            "note": "Frame too dark to identify anything."},
    },
    "f5": {
        "property_id": "maple-house", "date": "2026-10-30",
        "camera": "front-door", "ts": "2026-10-30T22:15:00",
        "description": "A person at the front door late in the "
                       "evening, on a date the house is closed and "
                       "vacant — nobody should be there at all.",
        "classification": {
            "scene": "unknown_person", "people_count": 1,
            "note": "One person at the front door of the closed house."},
    },
    "f6": {
        "property_id": "maple-house", "date": "2026-10-30",
        "camera": "front-door", "ts": "2026-10-30T10:30:00",
        "description": "A worker with cleaning supplies entering "
                       "mid-morning on the closed date, inside the "
                       "staged 10 AM-noon scheduled service window.",
        "classification": {
            "scene": "cleaner", "people_count": 1,
            "note": "One person with cleaning supplies entering.",
            "worker": "present"},
    },
    "f7": {
        "property_id": "cedar-2a", "date": "2026-10-28",
        "camera": "front-door", "ts": "2026-10-28T14:15:00",
        "description": "Post-turnover check after the cleaning "
                       "window: the studio door is standing open and "
                       "the lights are off.",
        "classification": {
            "scene": "nothing_notable", "people_count": 0,
            "note": "No people; the front door is open.",
            "door": "open", "lights": "off"},
    },
    "f8": {
        "property_id": "cedar-2a", "date": "2026-10-28",
        "camera": "front-door", "ts": "2026-10-28T14:20:00",
        "description": "Post-turnover check after the cleaning "
                       "window: door closed, lights off, nobody "
                       "around — the home is as it should be.",
        "classification": {
            "scene": "nothing_notable", "people_count": 0,
            "note": "No people; door closed and lights off.",
            "door": "closed", "lights": "off"},
    },
    "f9": {
        "property_id": "cedar-2a", "date": "2026-10-28",
        "camera": "front-door", "ts": "2026-10-28T11:02:00",
        "description": "A person still at the front door two "
                       "minutes after the 11 AM cleaning window "
                       "opens — not the cleaner; the departing guest "
                       "may not have left yet.",
        "classification": {
            "scene": "unknown_person", "people_count": 1,
            "note": "One person still at the front door as the "
                    "cleaning window opens."},
    },
    # Final-list batch (cases #10, #11, #17): frames staged for the
    # covered/shifted view reads, the stuck-at-the-door dwell pair,
    # and the sunset dark-entry checks.
    "f10": {
        "property_id": "cedar-1a", "date": "2026-10-30",
        "camera": "front-door", "ts": "2026-10-30T21:05:00",
        "description": "The frame is a flat dark blur: something is "
                       "over the lens on a Friday night, during a "
                       "one-night weekend stay at the unit.",
        "classification": {
            "scene": "nothing_notable", "people_count": 0,
            "note": "The frame is a flat dark blur; the lens "
                    "appears covered.",
            "view": "blocked"},
    },
    "f11": {
        "property_id": "cedar-2a", "date": "2026-10-28",
        "camera": "front-door", "ts": "2026-10-28T12:20:00",
        "description": "The camera now points down at the steps "
                       "instead of the door and driveway; a car is "
                       "partly visible at the frame's edge.",
        "classification": {
            "scene": "vehicle", "people_count": None,
            "note": "The frame points down at the steps; the "
                    "camera appears to have been turned.",
            "view": "shifted"},
    },
    "f12": {
        "property_id": "cedar-2a", "date": "2026-10-28",
        "camera": "front-door", "ts": "2026-10-28T15:37:00",
        "description": "One person standing at the studio door "
                       "inside the arrival window; the door is shut.",
        "classification": {
            "scene": "expected_guest", "people_count": 1,
            "note": "One person standing at the studio door.",
            "door": "closed"},
    },
    "f13": {
        "property_id": "cedar-2a", "date": "2026-10-28",
        "camera": "front-door", "ts": "2026-10-28T15:44:00",
        "description": "Seven minutes later the same person is "
                       "still at the studio door, and the door is "
                       "still shut — the stuck-at-the-door pattern.",
        "classification": {
            "scene": "expected_guest", "people_count": 1,
            "note": "The person is still at the studio door; the "
                    "door is still shut.",
            "door": "closed"},
    },
    "f14": {
        "property_id": "cedar-2a", "date": "2026-10-28",
        "camera": "front-door", "ts": "2026-10-28T15:51:00",
        "description": "Minutes later the door is open and the "
                       "guests are bringing their bags in — nobody "
                       "is stuck anymore.",
        "classification": {
            "scene": "expected_guest", "people_count": 2,
            "note": "Guests at the open door, bringing bags in.",
            "door": "open"},
    },
    "f15": {
        "property_id": "maple-house", "date": "2026-10-28",
        "camera": "front-door", "ts": "2026-10-28T18:52:00",
        "description": "Sunset check on Marcus's arrival day: the "
                       "entry is dark, no lights on at the front of "
                       "the house.",
        "classification": {
            "scene": "nothing_notable", "people_count": 0,
            "note": "Entry dark after sunset; no lights on at "
                    "the front.",
            "lights": "off"},
    },
    "f16": {
        "property_id": "cedar-2a", "date": "2026-10-28",
        "camera": "front-door", "ts": "2026-10-28T18:47:00",
        "description": "Sunset check on Dana's arrival day: the "
                       "studio entry is dark — and the fixtures "
                       "carry one dark night already, so this is "
                       "the second night running.",
        "classification": {
            "scene": "nothing_notable", "people_count": 0,
            "note": "The studio entry is dark after sunset.",
            "lights": "off"},
    },
    "f17": {
        "property_id": "maple-house", "date": "2026-10-28",
        "camera": "front-door", "ts": "2026-10-28T18:55:00",
        "description": "Sunset check variant: the porch light is "
                       "on at the entry — the home is lit for the "
                       "arriving guest.",
        "classification": {
            "scene": "nothing_notable", "people_count": 0,
            "note": "Porch light on at the entry.",
            "lights": "on"},
    },
    "f18": {
        "property_id": "maple-house", "date": "2026-10-28",
        "camera": "front-door", "ts": "2026-10-28T16:08:00",
        "description": "A person at the front door with a bag "
                       "beside them on Marcus's arrival day; the "
                       "door is shut — the frame a lockout report "
                       "would be checked against.",
        "classification": {
            "scene": "unknown_person", "people_count": 1,
            "note": "One person at the front door, bag beside "
                    "them.",
            "door": "closed"},
    },
    # Final-list batch 2 (cases #46, #32): the dog arriving with
    # the party, a lone dog-sized animal at night (which must NOT
    # fire the dog route), and the snow-check set — snow at an
    # empty home, snow with a guest due that day, clear ground, an
    # unsettled frame, and snow while a stay is in progress (the
    # morning check skips occupied homes quietly).
    "f19": {
        "property_id": "maple-house", "date": "2026-10-28",
        "camera": "front-door", "ts": "2026-10-28T15:58:00",
        "description": "Marcus's party arrives at Maple Street House "
                       "with a dog on a leash; the booking lists "
                       "no pet.",
        "classification": {
            "scene": "expected_guest", "people_count": 2,
            "note": "Two people with bags and a dog on a leash "
                    "at the front door.",
            "animal": "dog_sized"},
    },
    "f20": {
        "property_id": "maple-house", "date": "2026-10-28",
        "camera": "driveway", "ts": "2026-10-28T23:41:00",
        "description": "A dog-sized animal crosses the driveway "
                       "alone late at night — nobody's party, no "
                       "arrival context. The dog route must not "
                       "fire on this frame.",
        "classification": {
            "scene": "animal", "people_count": None,
            "note": "A dog-sized animal crosses the driveway "
                    "alone.",
            "animal": "dog_sized"},
    },
    "f21": {
        "property_id": "maple-house", "date": "2026-10-29",
        "camera": "front-door", "ts": "2026-10-29T10:30:00",
        "description": "Morning check at Maple Street House the day "
                       "after Marcus's stay (fixtures carry no "
                       "stay, arrival, or turnover that day): snow "
                       "still covers the walkway and driveway at "
                       "mid-morning.",
        "classification": {
            "scene": "nothing_notable", "people_count": 0,
            "note": "Snow still covers the walkway and the "
                    "driveway.",
            "ground": "snow_covered"},
    },
    "f22": {
        "property_id": "cedar-2a", "date": "2026-10-28",
        "camera": "front-door", "ts": "2026-10-28T10:15:00",
        "description": "Morning check at Studio 2A on Dana's "
                       "arrival day (a turnover is also on record): "
                       "snow still covers the walkway mid-morning, "
                       "with a guest due — the urgent variant.",
        "classification": {
            "scene": "nothing_notable", "people_count": 0,
            "note": "Snow still covers the walkway.",
            "ground": "snow_covered"},
    },
    "f23": {
        "property_id": "maple-house", "date": "2026-10-29",
        "camera": "front-door", "ts": "2026-10-29T10:35:00",
        "description": "Morning check variant: the walkway and "
                       "driveway at Maple Street House are bare — the "
                       "clear read that logs quietly.",
        "classification": {
            "scene": "nothing_notable", "people_count": 0,
            "note": "Walkway and driveway are bare.",
            "ground": "clear"},
    },
    "f24": {
        "property_id": "maple-house", "date": "2026-10-29",
        "camera": "front-door", "ts": "2026-10-29T10:40:00",
        "description": "Morning check variant: glare washes the "
                       "frame out and the ground state cannot be "
                       "settled — the fail-safe read that notifies.",
        "classification": {
            "scene": "nothing_notable", "people_count": 0,
            "note": "Glare washes out the frame; the ground "
                    "cannot be judged."},
    },
    "f25": {
        "property_id": "cedar-1a", "date": "2026-10-29",
        "camera": "front-door", "ts": "2026-10-29T10:20:00",
        "description": "Morning check while Alex is mid-stay at "
                       "Garden Unit 1A: snow covers the walkway, "
                       "but the home is occupied — the snow check "
                       "is for empty homes and skips this quietly.",
        "classification": {
            "scene": "nothing_notable", "people_count": 0,
            "note": "Snow covers the walkway.",
            "ground": "snow_covered"},
    },
    # Final-list batch 3 (cases #6, #23, #55). The gathering pair
    # f26/f27 come before f3 on the same patio the same evening:
    # with f3 they are a sustained run of estimates across 28
    # minutes, which is what the accumulation fires on — f3 alone
    # no longer does. f28 is the vendor-window frame with no worker
    # in it. f29-f33 are one long stay at Studio 2A: arrival-day
    # luggage (never counts), then boxes or belongings on three
    # distinct days (the move-in-signs pattern), then one more
    # boxes frame after the pattern is already flagged.
    "f26": {
        "property_id": "cedar-1a", "date": "2026-10-29",
        "camera": "backyard", "ts": "2026-10-29T21:20:00",
        "description": "People gathered on the patio in the evening, "
                       "mid-stay, more than the booked party of four "
                       "— the first frame of the evening's run.",
        "classification": {
            "scene": "large_gathering", "people_count": 9,
            "note": "A group gathered on the patio."},
    },
    "f27": {
        "property_id": "cedar-1a", "date": "2026-10-29",
        "camera": "backyard", "ts": "2026-10-29T21:35:00",
        "description": "Fifteen minutes later the patio group is "
                       "still there and a little larger.",
        "classification": {
            "scene": "large_gathering", "people_count": 10,
            "note": "The group on the patio is still there."},
    },
    "f28": {
        "property_id": "cedar-1a", "date": "2026-10-29",
        "camera": "front-door", "ts": "2026-10-29T15:25:00",
        "description": "Inside the handyman's stated 3-4 PM window "
                       "for Alex's leak: leaves blow across an empty "
                       "walkway; nobody at the door.",
        "classification": {
            "scene": "nothing_notable", "people_count": 0,
            "note": "Leaves blowing across an empty walkway.",
            "worker": "absent"},
    },
    "f29": {
        "property_id": "cedar-2a", "date": "2026-11-02",
        "camera": "front-door", "ts": "2026-11-02T14:10:00",
        "description": "Jordan's long stay begins: one person with "
                       "two suitcases at the studio door — ordinary "
                       "luggage, which never counts toward move-in "
                       "signs.",
        "classification": {
            "scene": "expected_guest", "people_count": 1,
            "note": "One person with two suitcases at the studio "
                    "door.",
            "carrying": "luggage"},
    },
    "f30": {
        "property_id": "cedar-2a", "date": "2026-11-03",
        "camera": "front-door", "ts": "2026-11-03T18:05:00",
        "description": "The next evening: one person carrying moving "
                       "boxes to the studio door.",
        "classification": {
            "scene": "expected_guest", "people_count": 1,
            "note": "One person carrying moving boxes to the door.",
            "carrying": "boxes_or_belongings"},
    },
    "f31": {
        "property_id": "cedar-2a", "date": "2026-11-04",
        "camera": "front-door", "ts": "2026-11-04T17:40:00",
        "description": "A second day: a stack of boxes and a floor "
                       "lamp carried in.",
        "classification": {
            "scene": "expected_guest", "people_count": 1,
            "note": "One person carrying a stack of boxes and a "
                    "floor lamp.",
            "carrying": "boxes_or_belongings"},
    },
    "f32": {
        "property_id": "cedar-2a", "date": "2026-11-05",
        "camera": "front-door", "ts": "2026-11-05T19:15:00",
        "description": "A third distinct day: two people carry a "
                       "mattress to the studio door — the frame that "
                       "completes the multi-day pattern.",
        "classification": {
            "scene": "expected_guest", "people_count": 2,
            "note": "Two people carrying a mattress to the door.",
            "carrying": "boxes_or_belongings"},
    },
    "f33": {
        "property_id": "cedar-2a", "date": "2026-11-05",
        "camera": "front-door", "ts": "2026-11-05T19:40:00",
        "description": "Later the same evening, more boxes — the "
                       "pattern is already flagged, so this logs.",
        "classification": {
            "scene": "expected_guest", "people_count": 1,
            "note": "One person carrying boxes to the door.",
            "carrying": "boxes_or_belongings"},
    },
}


def frames_for(property_id: str, date: str | None = None,
               camera: str | None = None) -> list[dict]:
    """The staged frame records on file for a property (optionally
    one date, optionally one camera), oldest first. Each record is
    the STAGED_FRAMES entry plus its frame_id. This is the staged
    stand-in for a real camera system's frame store: the plays that
    assemble "the latest frames" (guest lockout, guest feels unsafe)
    read from here, never from the live API."""
    out = []
    for fid, rec in STAGED_FRAMES.items():
        if rec["property_id"] != property_id:
            continue
        if date is not None and rec["date"] != date:
            continue
        if camera is not None and rec["camera"] != camera:
            continue
        out.append({"frame_id": fid, **rec})
    out.sort(key=lambda r: r["ts"])
    return out


class FixtureVision:
    """Staged vision: returns the recorded classification for a
    frame_id. An unknown frame_id returns None — an unrecognized
    frame must never be waved through as fine."""

    def __init__(self, staged: dict | None = None):
        if staged is None:
            staged = {fid: rec["classification"]
                      for fid, rec in STAGED_FRAMES.items()}
        self._staged = staged

    def classify(self, frame_id: str, image_bytes: bytes | None = None,
                 **_) -> Classification | None:
        rec = self._staged.get(frame_id)
        if rec is None:
            return None
        return Classification(scene=rec["scene"],
                              people_count=rec.get("people_count"),
                              note=rec.get("note", ""),
                              source="fixture",
                              door=rec.get("door", "unknown"),
                              lights=rec.get("lights", "unknown"),
                              view=rec.get("view", "unknown"),
                              ground=rec.get("ground", "unknown"),
                              animal=rec.get("animal", "unknown"),
                              carrying=rec.get("carrying", "unknown"),
                              worker=rec.get("worker", "unknown"))


class LiveVision:
    """Live vision via the Meta Model API — VERIFIED 2026-10-27.

    Image input on the Responses endpoint uses Responses-native
    content parts: {"type": "input_text", "text": ...} and
    {"type": "input_image", "image_url": "data:<media>;base64,..."}
    (the URL is a plain string, not an object). The chat-completions
    shape ({"type": "image_url", "image_url": {"url": ...}}) is
    rejected with HTTP 400 "input[1].content did not match any
    supported type" — verified live 2026-10-27, when the
    Responses-native shape returned HTTP 200 from muse-spark-1.3
    and the full classify path parsed a valid Classification.
    Fail-safe throughout: no API key, no image bytes, any transport
    error, or any unparsable reply returns None, which the play
    treats as an unclear frame.
    """

    def __init__(self, config: Config | None = None):
        self.config = config or Config.from_env()

    def classify(self, frame_id: str, image_bytes: bytes | None = None,
                 image_media_type: str = "image/png",
                 **_) -> Classification | None:
        cfg = self.config
        if not cfg.api_key or not cfg.base_url or not image_bytes:
            return None
        data_uri = "data:{};base64,{}".format(
            image_media_type, base64.b64encode(image_bytes).decode())
        payload = {
            "model": cfg.tier_fast,
            "input": [
                {"role": "system", "content": VISION_SYSTEM},
                {"role": "user", "content": [
                    {"type": "input_text",
                     "text": "Classify this exterior camera frame."},
                    {"type": "input_image",
                     "image_url": data_uri},
                ]},
            ],
        }
        req = urllib.request.Request(
            cfg.base_url.rstrip("/") + "/responses",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json",
                     "Authorization": f"Bearer {cfg.api_key}"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(
                    req, timeout=cfg.request_timeout_secs) as resp:
                body = json.loads(resp.read().decode("utf-8"))
        except (urllib.error.URLError, TimeoutError, ValueError, OSError):
            return None
        text = extract_text(body)
        cost = usage_cost(body, cfg.tier_fast, len(text))
        return parse_classification(text, source="live", cost_usd=cost)
