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

VISION_SYSTEM = (
    "You classify a single still frame from an exterior camera at a "
    "short-term rental property. Reply with ONLY a JSON object: "
    '{"scene": one of ["expected_guest", "cleaner", "unknown_person", '
    '"large_gathering", "vehicle", "animal", "nothing_notable", '
    '"unclear"], "people_count": integer or null, "note": one short '
    'sentence, "door": one of ["open", "closed", "unknown"], '
    '"lights": one of ["on", "off", "unknown"], '
    '"view": one of ["normal", "blocked", "shifted", "unknown"]}. '
    "You cannot identify who a person is — judge only the kind of "
    "scene. Judge the door and lights state only when a door or the "
    'home\'s lights are actually visible; otherwise report "unknown" '
    "for that field. Judge the view state from the frame itself: "
    '"blocked" when the lens appears covered or obstructed (tape, '
    'cloth, an object over it), "shifted" when the frame looks as '
    "if the camera has been turned or knocked (pointing at the "
    'ground, the sky, or a wall instead of the usual scene), '
    '"normal" when neither is the case, and "unknown" when the '
    "frame does not let you judge. If the frame is too dark, blurry, "
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
        return Classification(scene=scene, people_count=people,
                              note=str(obj.get("note", "")),
                              source=source, cost_usd=cost_usd,
                              door=door, lights=lights, view=view)
    except (ValueError, KeyError, TypeError):
        return None


# The staged frame world Play 7 runs on in the demo and the tests.
# Metadata only: what the frame depicts, in words, plus the staged
# classification FixtureVision returns for it. Wired to the fixture
# calendar in context.default_fixture_data (same properties/dates).
STAGED_FRAMES: dict[str, dict] = {
    "f1": {
        "property_id": "lockport-2n", "date": "2026-10-28",
        "camera": "front-door", "ts": "2026-10-28T15:42:00",
        "description": "Two adults with rolling suitcases at the "
                       "front door, mid-afternoon on arrival day.",
        "classification": {
            "scene": "expected_guest", "people_count": 2,
            "note": "Two people with suitcases at the front door."},
    },
    "f2": {
        "property_id": "lockport-2n", "date": "2026-10-28",
        "camera": "front-door", "ts": "2026-10-28T11:06:00",
        "description": "One person carrying a caddy of cleaning "
                       "supplies, entering just after checkout.",
        "classification": {
            "scene": "cleaner", "people_count": 1,
            "note": "One person with cleaning supplies entering."},
    },
    "f3": {
        "property_id": "lockport-1n", "date": "2026-10-29",
        "camera": "backyard", "ts": "2026-10-29T21:48:00",
        "description": "A large group gathered on the patio in the "
                       "evening, mid-stay, well beyond the booked "
                       "party of four.",
        "classification": {
            "scene": "large_gathering", "people_count": 11,
            "note": "About eleven people gathered on the patio."},
    },
    "f4": {
        "property_id": "peters-house", "date": "2026-10-28",
        "camera": "driveway", "ts": "2026-10-28T23:58:00",
        "description": "Near-black frame just before midnight; motion "
                       "triggered but nothing is identifiable.",
        "classification": {
            "scene": "unclear", "people_count": None,
            "note": "Frame too dark to identify anything."},
    },
    "f5": {
        "property_id": "peters-house", "date": "2026-10-30",
        "camera": "front-door", "ts": "2026-10-30T22:15:00",
        "description": "A person at the front door late in the "
                       "evening, on a date the house is closed and "
                       "vacant — nobody should be there at all.",
        "classification": {
            "scene": "unknown_person", "people_count": 1,
            "note": "One person at the front door of the closed house."},
    },
    "f6": {
        "property_id": "peters-house", "date": "2026-10-30",
        "camera": "front-door", "ts": "2026-10-30T10:30:00",
        "description": "A worker with cleaning supplies entering "
                       "mid-morning on the closed date, inside the "
                       "staged 10 AM-noon scheduled service window.",
        "classification": {
            "scene": "cleaner", "people_count": 1,
            "note": "One person with cleaning supplies entering."},
    },
    "f7": {
        "property_id": "lockport-2n", "date": "2026-10-28",
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
        "property_id": "lockport-2n", "date": "2026-10-28",
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
        "property_id": "lockport-2n", "date": "2026-10-28",
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
        "property_id": "lockport-1n", "date": "2026-10-30",
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
        "property_id": "lockport-2n", "date": "2026-10-28",
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
        "property_id": "lockport-2n", "date": "2026-10-28",
        "camera": "front-door", "ts": "2026-10-28T15:37:00",
        "description": "One person standing at the studio door "
                       "inside the arrival window; the door is shut.",
        "classification": {
            "scene": "expected_guest", "people_count": 1,
            "note": "One person standing at the studio door.",
            "door": "closed"},
    },
    "f13": {
        "property_id": "lockport-2n", "date": "2026-10-28",
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
        "property_id": "lockport-2n", "date": "2026-10-28",
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
        "property_id": "peters-house", "date": "2026-10-28",
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
        "property_id": "lockport-2n", "date": "2026-10-28",
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
        "property_id": "peters-house", "date": "2026-10-28",
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
        "property_id": "peters-house", "date": "2026-10-28",
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
                              view=rec.get("view", "unknown"))


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
