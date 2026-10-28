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
"people_count": int | null, "note": short string}. Anything that
does not parse to exactly that shape returns None, and callers treat
None exactly like an "unclear" frame: fail safe to a human.
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

VISION_SYSTEM = (
    "You classify a single still frame from an exterior camera at a "
    "short-term rental property. Reply with ONLY a JSON object: "
    '{"scene": one of ["expected_guest", "cleaner", "unknown_person", '
    '"large_gathering", "vehicle", "animal", "nothing_notable", '
    '"unclear"], "people_count": integer or null, "note": one short '
    "sentence}. You cannot identify who a person is — judge only the "
    "kind of scene. If the frame is too dark, blurry, or obstructed "
    'to judge, the scene is "unclear".'
)


@dataclass
class Classification:
    scene: str                      # one of SCENES
    people_count: int | None
    note: str
    source: str = "fixture"         # fixture | live
    cost_usd: float = 0.0


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
        return Classification(scene=scene, people_count=people,
                              note=str(obj.get("note", "")),
                              source=source, cost_usd=cost_usd)
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
}


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
                              source="fixture")


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
