"""Mapping answers: category geo_distance (built Oct 31, 2026).

"How far is X?", "How long to X?", "Where's the nearest pharmacy?":
answered with the distance and drive time from the listing, computed
from map data, never written by a model.

  question   deterministic parse of ONE question sentence into a named
             destination ("Grand Harbor Pier") or a place type from a
             closed vocabulary ("pharmacy", "Japanese restaurant"). The
             rest of the message must be greetings and thanks; any other
             content, a request, an issue word, a risky emoji, a stay or
             money ask (the Phase 0 guard), a barred type (medical,
             emergency, police, fuel), a personal destination ("my
             friend's house", a street address, a guest's name) holds.
  lookup     a provider interface (search a place, route to it). Offline
             tests use the fixture provider; OSMProvider calls Nominatim
             (search) and OSRM (route), cached on disk and rate-limited to
             one request per second across EVERY provider instance and
             process on the machine (RateGate: a shared lock file and
             last-request stamp; Nominatim's limit is per application,
             not per instance). ONLY the destination phrase (or the
             type's search phrase) and the listing's coarse coordinates
             leave the machine: never a guest's name, never the message,
             never reservation data.
  judgment   deterministic code. Named place: the candidates whose names
             carry every word of the destination, within NAMED_RADIUS_KM
             of the listing; if the two nearest are within 15% of each
             other's distance, or a better-known place of that name sits
             farther away, HOLD (ambiguous). Nearest of a type: the closest
             result by driving distance among the nearest few.
  answer     a fixed template with computed slots, rounded and phrased as
             approximations: "Hi! <Place> is about <X> miles away, roughly
             <Y> minutes by car without traffic." OSRM times are free-flow,
             so a live lookup always says so (audit ruling, Oct 31, 2026).
             An operator-pinned answer (times the operator checked, e.g. on
             Google Maps for a guest-relevant day and time) keeps the plain
             "roughly <Y> minutes by car." Under 0.3 miles: "Hi! <Place> is
             a short walk, about <Y> minutes." The listing's address never
             appears, and neither does a place name shaped like a street
             address ("45 Archer Ave", "Unit 2"): such a place holds.

Every failure is a HOLD: no answer goes, the message follows the
actuator's existing fallback path. Recall is the cost, never a wrong
answer.
"""
import fcntl
import json
import math
import os
import re
import tempfile
import time
import urllib.parse
import urllib.request
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from . import answer_text as T
from .hub_answers import reservation_problem, sha256

try:
    from .change_guard import guarded_class
except ImportError:  # pragma: no cover
    guarded_class = None

CATEGORY = "geo_distance"
MAX_MESSAGE_CHARS = 500
NAMED_RADIUS_KM = 80.0       # about 50 miles: a named place farther than this holds
AMBIGUITY_RATIO = 1.15       # the two nearest same-name candidates within 15%: hold
PROMINENCE_MARGIN = 0.10     # a better-known same-name place farther away: hold
SAME_PLACE_M = 150.0         # two results this close with one name are one place
TOO_CLOSE_M = 30.0           # "that's here": hold
MAX_ROUTE_RATIO = 4.0        # driving distance over straight-line distance: past this the data is suspect
TYPE_ROUTE_CANDIDATES = 3    # nearest-of-type: route the nearest few, keep the shortest drive
WALK_UNDER_MILES = 0.3
WALK_M_PER_MIN = 80.0        # about 3 mph
MILE_M = 1609.344
MAX_PLACE_NAME = 60


@dataclass(frozen=True)
class Place:
    name: str
    lat: float
    lon: float
    kinds: tuple = ()          # ("amenity:pharmacy", ...): OSM class:type
    importance: float = 0.0
    cuisine: tuple = ()
    osm_id: str = ""


@dataclass(frozen=True)
class Route:
    distance_m: float
    duration_s: float


def haversine_m(a, b):
    lat1, lon1, lat2, lon2 = map(math.radians, (a[0], a[1], b[0], b[1]))
    h = math.sin((lat2 - lat1) / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2) ** 2
    return 2 * 6371008.8 * math.asin(math.sqrt(h))


# ---------- the closed type vocabulary ----------

@dataclass(frozen=True)
class TypeSpec:
    id: str
    label: str            # how the answer names it: "The nearest <label>, <Place>, ..."
    query: str            # the only words sent to the geocoder for this type
    kinds: tuple          # accepted OSM class:type values
    cuisine: tuple = ()   # if set, the result's cuisine must carry one of these
    radius_km: float = 25.0


_R = "(?:restaurants?|places?|spots?|joints?|food)"
TYPES = [
    (r"grocery(?:\s+stores?)?|groceries|supermarkets?|food\s+stores?",
     TypeSpec("grocery", "grocery store", "supermarket", ("shop:supermarket", "shop:grocery"))),
    (r"pharmac(?:y|ies)|drug\s?stores?|chemists?",
     TypeSpec("pharmacy", "pharmacy", "pharmacy", ("amenity:pharmacy", "shop:chemist"))),
    (r"(?:train|metra|railway|rail|commuter\s+rail|commuter\s+train)\s+stations?|metra|train\s+stops?",
     TypeSpec("train_station", "train station", "train station",
              ("railway:station", "railway:halt", "public_transport:station"), radius_km=40.0)),
    (r"coffee\s+(?:shops?|places?|spots?)|cafes?",
     TypeSpec("coffee_shop", "coffee shop", "cafe", ("amenity:cafe",))),
    (r"(?:japanese|sushi|ramen)\s+" + _R + r"|sushi",
     TypeSpec("japanese", "Japanese restaurant", "japanese restaurant", ("amenity:restaurant",),
              ("japanese", "sushi", "ramen"))),
    (r"mexican\s+" + _R + r"|taquerias?|taco\s+(?:places?|spots?|shops?|trucks?)|tacos",
     TypeSpec("mexican", "Mexican restaurant", "mexican restaurant", ("amenity:restaurant", "amenity:fast_food"),
              ("mexican", "tacos", "taco"))),
    (r"pizza(?:\s+(?:places?|spots?|joints?|shops?|restaurants?))?|pizzerias?",
     TypeSpec("pizza", "pizza place", "pizza", ("amenity:restaurant", "amenity:fast_food"), ("pizza",))),
    (r"(?:italian)\s+" + _R, TypeSpec("italian", "Italian restaurant", "italian restaurant",
                                      ("amenity:restaurant",), ("italian",))),
    (r"(?:chinese)\s+" + _R, TypeSpec("chinese", "Chinese restaurant", "chinese restaurant",
                                      ("amenity:restaurant",), ("chinese",))),
    (r"(?:thai)\s+" + _R, TypeSpec("thai", "Thai restaurant", "thai restaurant", ("amenity:restaurant",), ("thai",))),
    (r"(?:indian)\s+" + _R, TypeSpec("indian", "Indian restaurant", "indian restaurant",
                                     ("amenity:restaurant",), ("indian",))),
    (r"restaurants?|places?\s+to\s+eat", TypeSpec("restaurant", "restaurant", "restaurant", ("amenity:restaurant",))),
    (r"parks?|playgrounds?", TypeSpec("park", "park", "park", ("leisure:park",))),
    (r"(?:movie\s+)?theaters?|movie\s+theatres?|cinemas?|movies",
     TypeSpec("cinema", "movie theater", "cinema", ("amenity:cinema",))),
    (r"librar(?:y|ies)", TypeSpec("library", "library", "library", ("amenity:library",))),
    (r"hardware\s+stores?|home\s+improvement\s+stores?",
     TypeSpec("hardware", "hardware store", "hardware store", ("shop:hardware", "shop:doityourself"))),
    (r"convenience\s+stores?|corner\s+stores?",
     TypeSpec("convenience", "convenience store", "convenience store", ("shop:convenience",))),
    (r"laundromats?", TypeSpec("laundromat", "laundromat", "laundromat", ("shop:laundry",))),
]
_TYPE_RES = [(re.compile(r"^(?:" + rx + r")$"), spec) for rx, spec in TYPES]

# Never answered, whatever the data says: medical, emergency, police, fuel
# ("gas" is also a safety word in the actuator), weapons, cannabis, adult.
BARRED_DEST = re.compile(
    r"\b(?:hospitals?|urgent\s+care|emergency|er|e\.r|clinics?|doctors?|dr|medical|medic\w*|dentists?|vets?"
    r"|veterinar\w*|police|cops?|sheriff|fire\s+(?:station|department|dept)|firehouse|gas|fuel|petrol|shelters?"
    r"|cannabis|dispensar\w*|weed|marijuana|smoke\s+shops?|vape\w*|guns?|firearms?|ammo|shooting\s+range"
    r"|strip\s+clubs?|adult|casinos?|atms?|banks?|currency)\b")
# A person's place or a location the guest has not named as a public place.
_PERSONAL = frozenset("""my our your his her their me us him them i we you it this that there here home house place
apartment apt unit studio condo room airbnb rental property building hotel work office job friend friends mom
mother dad father parents family cousin sister brother aunt uncle grandma grandpa someone somebody""".split())
_VAGUE = frozenset("downtown town city center centre area airport station stop beach lake mall store shop".split())
_STREET = re.compile(r"\b(?:street|st|avenue|ave|road|rd|boulevard|blvd|lane|ln|drive|dr|court|ct|way|highway|hwy"
                     r"|route|rte|parkway|pkwy|terrace|ter|circle|cir|place|pl|plaza|suite|ste|zip|address)\b")
# A place NAME shaped like a street address (audit G1, Nov 1, 2026): OSM
# sometimes names an unnamed point by its address ("45 Archer Ave"). Read on
# the folded name and on a rendered answer; the template's own words after
# a number (miles, minutes, hour, by car, away) are none of these. A
# business name with a street word and no number ("Oak Street Coffee") is
# not an address.
ADDRESS_RES = (
    # a house number, up to three words, a street suffix: "45 archer ave", "12 main st", "45th st"
    re.compile(r"\b\d+(?:st|nd|rd|th)?\s+(?:[a-z][a-z'.\-]*\s+){0,3}(?:street|st|avenue|ave|road|rd|boulevard"
               r"|blvd|lane|ln|drive|dr|court|ct|way|highway|hwy|parkway|pkwy|terrace|ter|circle|cir|place|pl"
               r"|plaza)\b"),
    # a unit number: "unit 2", "apt 3", "suite #4", "ste 5"
    re.compile(r"\b(?:unit|apartment|apt|suite|ste)\s*#?\s*\d"),
)


def address_like(text):
    """The first address-shaped stretch of `text` (folded), or None. A name
    that opens with a number and carries any street word counts too."""
    t = T.fold(str(text or ""))
    for rx in ADDRESS_RES:
        m = rx.search(t)
        if m:
            return m.group(0)
    if re.match(r"\s*#?\d", t) and _STREET.search(t):
        return t.strip()
    return None


_DEST_CHARS = re.compile(r"^[a-z0-9][a-z0-9' &.\-]*$")
_STOP = frozenset({"the", "a", "an", "of", "at", "and", "&", "in", "on"})

# ---------- the question forms ----------

_TAIL = (r"(?:\s+(?:from\s+(?:here|us|the\s+(?:house|unit|place|apartment|apt|studio|property|building|airbnb"
         r"|rental))|away|by\s+car|driving|from\s+where\s+we(?:'re|\s+are)\s+staying))*")
_NAMED_FORMS = [re.compile(p) for p in (
    r"^how\s+far\s+(?:away\s+)?(?:is|'s)\s+(?:it\s+)?(?:to\s+|from\s+here\s+to\s+)?(?P<dest>.+?)" + _TAIL + r"$",
    r"^how\s+far\s+(?:are\s+we|is\s+the\s+(?:house|unit|place|apartment|studio|property))\s+from\s+(?P<dest>.+?)"
    + _TAIL + r"$",
    r"^how\s+far\s+(?:to|away)\s+(?P<dest>.+?)" + _TAIL + r"$",
    r"^how\s+long\s+(?:(?:does|would|will|is)\s+it\s+(?:take\s+)?)?(?:(?:to|of\s+a)\s+)?(?:(?:get|drive|go|ride"
    r"|walk)\s+)?(?:to|over\s+to)\s+(?P<dest>.+?)" + _TAIL + r"$",
    r"^how\s+long\s+is\s+(?:the|a)\s+(?:drive|ride|trip|walk)\s+to\s+(?P<dest>.+?)" + _TAIL + r"$",
    r"^how\s+(?:close|near)\s+(?:is|are\s+we\s+to|is\s+it\s+to)\s+(?P<dest>.+?)" + _TAIL + r"$",
    r"^(?:what'?s|what\s+is)\s+the\s+(?:distance|drive|drive\s+time)\s+(?:to|from\s+here\s+to)\s+(?P<dest>.+?)"
    + _TAIL + r"$",
    r"^how\s+many\s+(?:miles|minutes)\s+(?:away\s+)?(?:is\s+it\s+)?(?:to|from\s+here\s+to|is|away\s+is)\s+"
    r"(?P<dest>.+?)" + _TAIL + r"$",
    r"^is\s+(?P<dest>.+?)\s+(?:far|close|nearby|near\s+here|close\s+by)" + _TAIL + r"$",
    r"^distance\s+to\s+(?P<dest>.+?)" + _TAIL + r"$",
)]
_NEAR_TAIL = (r"(?:\s+(?:nearby|near\s+here|near\s+us|close\s+by|around\s+here|in\s+the\s+area|to\s+(?:here|us"
              r"|the\s+(?:house|unit|place|apartment|studio|property))|from\s+(?:here|the\s+(?:house|unit|place"
              r"|apartment|studio|property))|by\s+car|driving))*")
_TYPE_FORMS = [re.compile(p) for p in (
    r"^(?:where\s+is|where'?s|what\s+is|what'?s|which\s+is|how\s+far\s+(?:away\s+)?is|how\s+close\s+is"
    r"|is\s+there|how\s+far\s+to)\s+(?:the|a|an)\s+(?:nearest|closest)\s+(?P<type>.+?)" + _NEAR_TAIL + r"$",
    r"^(?:is\s+there|are\s+there|any)\s+(?:a\s+|an\s+|any\s+)?(?P<type>.+?)\s+(?:nearby|near\s+here|near\s+us"
    r"|close\s+by|around\s+here|close|in\s+the\s+area)" + r"$",
    r"^(?:the\s+)?(?:nearest|closest)\s+(?P<type>.+?)" + _NEAR_TAIL + r"$",
)]
_LEAD_DEST = re.compile(r"^(?:the\s+|a\s+|an\s+)?(?:(?:nearest|closest)\s+)?")

# Question vocabulary for whole-message coverage (the destination phrase
# itself is masked out before coverage is read).
GEO_COVER = re.compile(r"\b(?:" + "|".join(sorted((
    "how far", "far", "away", "how long", "long", "take", "takes", "get", "drive", "driving", "walk", "walking",
    "go", "ride", "trip", "distance", "drive time", "close", "closer", "closest", "near", "nearest", "nearby",
    "near here", "close by", "around here", "in the area", "from here", "here", "miles", "mile", "minutes",
    "minute", "by car", "car", "is there", "are there", "located", "the house", "the unit", "the place",
    "the apartment", "the studio", "the property", "the building", "the airbnb", "the rental", "how many",
    "from where we're staying", "from where we are staying", "staying", "over", "of a"),
    key=len, reverse=True)) + r")\b(?!')")


@dataclass(frozen=True)
class Parsed:
    mode: str             # "named" | "type"
    dest: str             # the destination phrase as the guest wrote it (folded)
    type_spec: object     # TypeSpec for mode "type"
    sentence: str


def type_for(phrase):
    p = re.sub(r"\s+", " ", (phrase or "").strip(" ?.!,"))
    for rx, spec in _TYPE_RES:
        if rx.match(p):
            return spec
    return None


def parse_question(sentence):
    """Parsed for a folded, courtesy-stripped question sentence, or None."""
    s = re.sub(r"\s+", " ", sentence.strip()).rstrip(" ?.!,").strip()
    s = re.sub(r"^(?:so|and|also|btw|quick\s+one|quick\s+question)[\s,:-]+", "", s)
    for rx in _TYPE_FORMS:
        m = rx.match(s)
        if m:
            spec = type_for(m.group("type"))
            if spec is not None:
                return Parsed("type", m.group("type").strip(), spec, s)
    for rx in _NAMED_FORMS:
        m = rx.match(s)
        if m:
            dest = m.group("dest").strip(" ,")
            core = _LEAD_DEST.sub("", dest, count=1)
            spec = type_for(core)
            if spec is not None and (dest != core or core == dest):
                return Parsed("type", core, spec, s)
            return Parsed("named", dest, None, s)
    return None


def _tokens(text):
    return [t for t in re.findall(r"[a-z0-9]+(?:'[a-z]+)?", T.fold(text)) if t not in _STOP]


def destination_problem(dest, forbidden_tokens=()):
    """Why a named destination phrase may not be looked up, or None."""
    d = T.fold(dest)
    if not d or not _DEST_CHARS.match(d):
        return "the destination has characters a place name does not"
    toks = d.replace("&", " ").split()
    core = [t for t in toks if t not in _STOP]
    if not core or len(toks) > 6:
        return "the destination is not a short place name"
    if BARRED_DEST.search(d):
        return f"a barred kind of place: {BARRED_DEST.search(d).group(0)!r}"
    if any(t in _PERSONAL for t in toks):
        return "a personal or unnamed destination"
    for i, t in enumerate(toks):
        if t.endswith("'s") and i + 1 < len(toks) and toks[i + 1] in _PERSONAL:
            return "a person's place"
    if len(core) == 1 and core[0] in _VAGUE:
        return "too vague to look up"
    if _STREET.search(d) or re.match(r"^\d", d) or re.search(r"\d{3,}", d):
        return "a street address"
    names = {T.fold(n) for n in forbidden_tokens if n and len(str(n)) >= 2}
    if any(t.strip("'s") in names or t in names for t in toks):
        return "the destination carries the guest's name"
    return None


# ---------- providers ----------

class ProviderError(Exception):
    pass


def _retry_eperm(fn, sleep=time.sleep, waits=(5, 10, 20, 30, 45, 60)):
    """The VM's intermittent EPERM on write (AGENTS.md, Oct 30, 2026): retry
    the whole atomic write with backoff, about 3 minutes in all."""
    for w in tuple(waits) + (None,):
        try:
            return fn()
        except PermissionError:
            if w is None:
                raise
            sleep(w)


def write_json_atomic(path, obj, sleep=time.sleep):
    def go():
        d = os.path.dirname(os.path.abspath(path))
        fd, tmp = tempfile.mkstemp(prefix=".tmp-", dir=d)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(obj, fh, sort_keys=True, indent=1)
            os.chmod(tmp, 0o600)
            os.replace(tmp, path)
        except BaseException:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise
    _retry_eperm(go, sleep=sleep)


RATE_SCHEMA = "comfi-autopilot/osm-rate/v1"
DEFAULT_RATE_FILE = "~/.cache/comfi-autopilot/osm-rate.json"


def default_rate_path():
    """One rate file per machine, whatever cache a provider uses: the
    Nominatim limit is per application, not per cache or per instance.
    JEV_OSM_RATE_FILE overrides the path."""
    return os.path.expanduser(os.environ.get("JEV_OSM_RATE_FILE") or DEFAULT_RATE_FILE)


class RateGate:
    """At most one map request per `min_interval` seconds for every
    OSMProvider sharing `path`: other instances in this process and other
    processes on this machine alike (Nominatim's usage policy is an
    absolute 1 request per second per application, and the actuator glue
    builds a new provider per message).

    An exclusive flock on `<path>.lock` is held across the whole request,
    so requests run one at a time. `path` stores when the last request
    started and, once it finished, when it ended (atomic write with the
    VM's EPERM retry); the next request waits `min_interval` from there.
    The clock is wall time, since a monotonic reading means nothing in
    another process; a stamp ahead of the clock (a step backwards) or an
    unreadable stamp waits the full interval. A gate that stays locked past
    `lock_timeout` seconds raises ProviderError: the caller holds rather
    than sending a request outside the gate."""

    def __init__(self, path, min_interval=1.0, clock=time.time, sleep=time.sleep, lock_timeout=60.0):
        self.path = path
        self.min_interval = float(min_interval)
        self.clock, self.sleep = clock, sleep
        self.lock_timeout = float(lock_timeout)

    def _last(self):
        try:
            with open(self.path, encoding="utf-8") as fh:
                return float(json.load(fh)["last_request_at"])
        except FileNotFoundError:
            return None
        except (OSError, ValueError, KeyError, TypeError):
            return math.inf  # unreadable: wait the full interval

    def _stamp(self):
        write_json_atomic(self.path, {"schema": RATE_SCHEMA, "last_request_at": self.clock()}, sleep=self.sleep)

    def _lock(self, fd):
        deadline = time.monotonic() + self.lock_timeout
        while True:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                return
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    raise ProviderError("the shared map rate gate stayed locked") from None
                time.sleep(0.05)

    @contextmanager
    def slot(self):
        """Wait for this request's turn, then hold the gate until it ends."""
        os.makedirs(os.path.dirname(os.path.abspath(self.path)), mode=0o700, exist_ok=True)
        fd = os.open(self.path + ".lock", os.O_RDWR | os.O_CREAT, 0o600)
        try:
            self._lock(fd)
            last = self._last()
            if last is not None:
                elapsed = self.clock() - last
                wait = self.min_interval if elapsed < 0 else self.min_interval - elapsed
                if wait > 0:
                    self.sleep(min(wait, self.min_interval))
            self._stamp()  # a request that dies mid-flight still counts
            try:
                yield
            finally:
                self._stamp()  # the next request waits from this one's end
        finally:
            os.close(fd)  # closing the descriptor releases the lock


class OSMProvider:
    """Nominatim search + OSRM driving route, behind a disk cache and the
    shared one-request-per-second RateGate. The query string is the
    destination phrase (or a type's fixed search words) and nothing else.
    The listing's coordinates go out only coarsened: the search box is
    rounded to 0.1 degree, the route origin to 3 decimals (about 100 m)."""
    name = "osm"
    USER_AGENT = "ComfiAutopilot/0.1 (deterministic distance answers; staging)"

    def __init__(self, cache_path, nominatim="https://nominatim.openstreetmap.org",
                 osrm="https://router.project-osrm.org", min_interval=1.0, ttl=timedelta(days=30),
                 http_get=None, clock=time.time, sleep=time.sleep, now=None, timeout=10, retries=1,
                 rate_path=None):
        self.cache_path = cache_path
        self.nominatim = nominatim.rstrip("/")
        self.osrm = osrm.rstrip("/")
        self.min_interval = float(min_interval)
        self.ttl = ttl
        self.http_get = http_get or self._urlopen
        self.clock, self.sleep = clock, sleep
        self.now = now or (lambda: datetime.now(timezone.utc))
        self.timeout = timeout
        self.retries = int(retries)
        self.gate = RateGate(rate_path or default_rate_path(), self.min_interval, clock=clock, sleep=sleep)
        self.sent = []

    # -- cache --
    def _load(self):
        try:
            with open(self.cache_path, encoding="utf-8") as fh:
                doc = json.load(fh)
            return doc if isinstance(doc, dict) and isinstance(doc.get("entries"), dict) else {"entries": {}}
        except (FileNotFoundError, ValueError):
            return {"entries": {}}

    def _cached(self, key):
        e = self._load()["entries"].get(key)
        if not isinstance(e, dict):
            return None
        try:
            at = datetime.fromisoformat(e["at"])
        except (KeyError, TypeError, ValueError):
            return None
        return e.get("value") if self.now() - at <= self.ttl else None

    def _store(self, key, value):
        doc = self._load()
        doc["entries"][key] = {"at": self.now().isoformat(timespec="seconds"), "value": value}
        doc["schema"] = "comfi-autopilot/geo-cache/v1"
        write_json_atomic(self.cache_path, doc, sleep=self.sleep)

    # -- http --
    def _urlopen(self, url):
        req = urllib.request.Request(url, headers={"User-Agent": self.USER_AGENT, "Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=self.timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))

    def _get(self, url):
        """One GET through the shared rate gate, at least min_interval after
        the previous request by any provider. A transient failure (this VM's
        egress proxy drops connections at random) is retried `retries`
        times, each retry through the gate again; then the caller holds."""
        last = None
        for _ in range(1 + max(0, self.retries)):
            try:
                with self.gate.slot():
                    return self.http_get(url)
            except Exception as e:  # network, HTTP error, bad JSON, a gate that stayed locked
                last = e
        raise ProviderError(f"{type(last).__name__}: {last}"[:200]) from None

    def search(self, query, near, radius_km, limit=10):
        lat, lon = round(near[0], 1), round(near[1], 1)
        dlat = radius_km / 111.0
        dlon = radius_km / max(1e-6, 111.0 * math.cos(math.radians(lat)))
        box = ",".join(f"{v:.1f}" for v in (lon - dlon, lat + dlat, lon + dlon, lat - dlat))
        params = {"q": query, "format": "jsonv2", "limit": str(int(limit)), "viewbox": box, "bounded": "1",
                  "extratags": "1", "addressdetails": "0", "namedetails": "0"}
        url = f"{self.nominatim}/search?{urllib.parse.urlencode(params)}"
        key = "search:" + url
        rows = self._cached(key)
        if rows is None:
            self.sent.append(("search", query))
            rows = self._get(url)
            if not isinstance(rows, list):
                raise ProviderError("search did not return a list")
            self._store(key, rows)
        out = []
        for r in rows:
            try:
                extra = r.get("extratags") or {}
                out.append(Place(
                    name=str(r.get("name") or "").strip(), lat=float(r["lat"]), lon=float(r["lon"]),
                    kinds=(f"{r.get('category') or r.get('class')}:{r.get('type')}",),
                    importance=float(r.get("importance") or 0.0),
                    cuisine=tuple(c.strip().lower() for c in str(extra.get("cuisine") or "").split(";") if c.strip()),
                    osm_id=f"{r.get('osm_type')}/{r.get('osm_id')}"))
            except (KeyError, TypeError, ValueError):
                continue
        return out

    def route(self, origin, dest):
        o = (round(origin[0], 3), round(origin[1], 3))
        url = (f"{self.osrm}/route/v1/driving/{o[1]:.3f},{o[0]:.3f};{dest.lon:.6f},{dest.lat:.6f}"
               "?overview=false&alternatives=false&steps=false")
        key = "route:" + url
        doc = self._cached(key)
        if doc is None:
            self.sent.append(("route", dest.name))
            doc = self._get(url)
            self._store(key, doc)
        try:
            if doc.get("code") != "Ok" or not doc.get("routes"):
                return None
            r = doc["routes"][0]
            return Route(float(r["distance"]), float(r["duration"]))
        except (AttributeError, KeyError, TypeError, ValueError, IndexError):
            return None


# ---------- judgment ----------

def _name_shape_ok(name):
    n = str(name or "")
    if not n or len(n) > MAX_PLACE_NAME or len(n.split()) > 8:
        return False
    if any(d in n for d in T.DASHES) or re.search(r"https?:|www\.|@|\d{3,}", n, re.I):
        return False
    return bool(re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9 '&.,\-]*", n.replace("’", "'")))


def _name_ok(name):
    """A name the template may carry: the shape rules, and not an address."""
    return _name_shape_ok(name) and address_like(name) is None


def _dedupe(cands, origin):
    """One entry per real place: same normalized name within SAME_PLACE_M."""
    out = []
    for c in sorted(cands, key=lambda p: (haversine_m(origin, (p.lat, p.lon)), p.osm_id)):
        if any(T.fold(c.name) == T.fold(o.name) and haversine_m((c.lat, c.lon), (o.lat, o.lon)) < SAME_PLACE_M
               for o in out):
            continue
        out.append(c)
    return out


def _route_ok(route, straight_m):
    if route is None or route.distance_m <= 0 or route.duration_s <= 0:
        return False
    if straight_m > 500 and route.distance_m > MAX_ROUTE_RATIO * straight_m:
        return False
    return route.distance_m >= 0.8 * straight_m


def fmt_miles(meters):
    mi = meters / MILE_M
    if mi < 1:
        v = round(mi, 1)
        return f"{v:.1f} miles"
    if mi < 10:
        v = round(mi * 2) / 2
    elif mi < 30:
        v = float(round(mi))
    else:
        v = float(round(mi / 5) * 5)
    s = f"{v:.1f}".rstrip("0").rstrip(".")
    return f"{s} mile" if s == "1" else f"{s} miles"


def fmt_minutes(minutes):
    m = max(1, int(round(minutes)))
    if m >= 10:
        m = int(round(m / 5.0) * 5)
    if m < 60:
        return "1 minute" if m == 1 else f"{m} minutes"
    h, rest = divmod(m, 60)
    hours = "1 hour" if h == 1 else f"{h} hours"
    return hours if rest == 0 else f"{hours} {rest} minutes"


def render(place_label, route, pinned=False):
    """The answer text, from the template and computed slots only. A live
    lookup's drive time is OSRM's free-flow time, so it says "without
    traffic"; an operator-pinned answer (`pinned`, times the operator
    checked) keeps the plain wording. The short walk needs no qualifier."""
    if route.distance_m / MILE_M < WALK_UNDER_MILES:
        return f"Hi! {place_label} is a short walk, about {fmt_minutes(route.distance_m / WALK_M_PER_MIN)}."
    return f"Hi! {place_label} is about {fmt_miles(route.distance_m)} away, roughly " \
           f"{fmt_minutes(route.duration_s / 60.0)} by car{'' if pinned else ' without traffic'}."


@dataclass(frozen=True)
class GeoPlan:
    verdict: str                 # "answer" | "hold"
    reason: str                  # short code (answer, coverage, request, safety_symbol, barred, ...)
    detail: str = ""
    text: object = None
    sha256: object = None
    mode: object = None
    dest: object = None
    type_id: object = None
    place: object = None
    route: object = None
    sent: tuple = ()
    override: bool = False

    def as_log(self):
        return {"category": CATEGORY, "verdict": self.verdict, "reason": self.reason, "detail": self.detail,
                "text": self.text, "text_sha256": self.sha256, "mode": self.mode, "dest": self.dest,
                "type_id": self.type_id, "place": self.place, "route": self.route,
                "sent": [list(s) for s in self.sent], "override": self.override}


def _hold(reason, detail="", **kw):
    return GeoPlan("hold", reason, detail, **kw)


def message_problem(message):
    """The repo's own whole-message checks, shared by both engines' callers:
    (reason, detail) or None, plus the parsed question."""
    raw = message or ""
    if len(raw) > MAX_MESSAGE_CHARS:
        return ("too_long", f"{len(raw)} chars"), None
    sym = T.risky_symbols(raw)
    if sym:
        return ("safety_symbol", f"symbols {''.join(sym)!r}"), None
    if guarded_class is None:
        return ("guard_unavailable", "jev.change_guard is not importable"), None
    g = guarded_class(raw)
    if g:
        return ("stay_or_money", f"phase0 guard: {g}"), None
    text = T.fold(T.guest_words(raw))
    asks = T.request_hits(text)
    if asks:
        return ("request", f"request or issue: {', '.join(asks)[:120]}"), None
    content = T.content_sentences(text)
    if len(content) != 1:
        return ("not_one_question", f"{len(content)} content sentences"), None
    sent = content[0]
    if not T.is_question(sent):
        return ("not_a_question", sent[:80]), None
    parsed = parse_question(T.strip_courtesy(sent))
    if parsed is None:
        return ("no_geo_form", sent[:80]), None
    masked = mask_destination(text, parsed.dest)
    if masked is None:
        return ("coverage", "destination not found in the message"), None
    gaps = T.coverage_gaps(masked, GEO_COVER)
    if gaps:
        return ("coverage", f"not covered: {', '.join(gaps)}"[:200]), None
    return None, parsed


def mask_destination(folded_text, dest):
    """folded_text with the destination phrase blanked (whole words), or None."""
    m = re.search(r"(?<![a-z0-9'])" + re.escape(dest) + r"(?![a-z0-9'])", folded_text or "")
    if not m:
        return None
    return folded_text[:m.start()] + " " * (m.end() - m.start()) + folded_text[m.end():]


def override_key(property_id, dest):
    return f"{property_id}|{' '.join(_tokens(dest))}"


def plan(message, property_id, origin, provider, *, reservation=None, scope=None, excluded=(),
         forbidden_tokens=(), overrides=None, parsed=None, check_message=True):
    """The one function that produces a mapping answer, or a hold.

    check_message False is for a caller that has already run the same
    whole-message checks and passes their `parsed` (the actuator glue, which
    runs its own gates first); the default runs them here."""
    if scope is not None and property_id not in scope:
        return _hold("scope", f"{property_id} is not one of the single units in scope")
    if property_id in set(excluded or ()):
        return _hold("excluded", "this listing never gets an answer")
    why = reservation_problem(reservation)
    if why:
        return _hold("reservation", why)
    if check_message or parsed is None:
        prob, parsed = message_problem(message)
        if prob:
            return _hold(*prob)
    if parsed.mode == "named":
        p = destination_problem(parsed.dest, forbidden_tokens)
        if p:
            return _hold("destination", p, mode="named", dest=parsed.dest)
    if origin is None or len(origin) != 2:
        return _hold("no_origin", "no listing coordinates on file", mode=parsed.mode, dest=parsed.dest)
    common = dict(mode=parsed.mode, dest=parsed.dest,
                  type_id=parsed.type_spec.id if parsed.type_spec else None)
    ov = (overrides or {}).get(override_key(property_id, parsed.dest))
    if ov is not None:
        return _from_override(ov, common)
    sent_before = len(getattr(provider, "sent", []))
    try:
        if parsed.mode == "type":
            res = _nearest_of_type(parsed.type_spec, origin, provider, common)
        else:
            res = _named(parsed.dest, origin, provider, common)
    except ProviderError as e:
        res = _hold("lookup_failed", str(e), **common)
    sent = tuple(tuple(s) for s in getattr(provider, "sent", [])[sent_before:])
    return GeoPlan(**{**res.__dict__, "sent": sent})


def _from_override(ov, common):
    """An operator-pinned answer for this listing and destination (the
    correction flow): used only while its hash matches what was pinned."""
    body = {k: ov.get(k) for k in ("place", "distance_m", "duration_s")}
    if ov.get("confirmed_sha256") != sha256(json.dumps(body, sort_keys=True)):
        return _hold("override_tampered", "a pinned override no longer matches its hash", **common)
    if not _name_ok(body["place"]):
        return _hold("place_name", "the pinned place name fails the name checks", **common)
    try:
        route = Route(float(body["distance_m"]), float(body["duration_s"]))
    except (TypeError, ValueError):
        return _hold("override_tampered", "a pinned override is unreadable", **common)
    text = render(body["place"], route, pinned=True)
    return GeoPlan("answer", "answer", "operator-pinned place", text, sha256(text), place={"name": body["place"]},
                   route={"distance_m": route.distance_m, "duration_s": route.duration_s}, override=True, **common)


def _answer(label, place, route, common, detail):
    text = render(label, route, pinned=False)  # a live lookup: free-flow time
    return GeoPlan("answer", "answer", detail, text, sha256(text),
                   place={"name": place.name, "lat": place.lat, "lon": place.lon, "osm_id": place.osm_id,
                          "kinds": list(place.kinds)},
                   route={"distance_m": route.distance_m, "duration_s": route.duration_s}, **common)


def _named(dest, origin, provider, common):
    want = set(_tokens(dest))
    cands = provider.search(dest, origin, NAMED_RADIUS_KM, limit=10)
    if not cands:
        return _hold("not_found", f"no result for {dest!r}", **common)
    exact = [c for c in cands if " ".join(_tokens(c.name)) == " ".join(_tokens(dest))]
    plausible = exact or [c for c in cands if want <= set(_tokens(c.name))]
    near = [c for c in plausible if haversine_m(origin, (c.lat, c.lon)) <= NAMED_RADIUS_KM * 1000.0]
    if not plausible:
        return _hold("not_found", f"no result named like {dest!r}", **common)
    if not near:
        return _hold("too_far", f"{dest!r} is past {NAMED_RADIUS_KM:.0f} km", **common)
    near = _dedupe(near, origin)
    d1 = haversine_m(origin, (near[0].lat, near[0].lon))
    if d1 < TOO_CLOSE_M:
        return _hold("too_close", "the result is at the listing itself", **common)
    if len(near) >= 2:
        d2 = haversine_m(origin, (near[1].lat, near[1].lon))
        if d2 <= AMBIGUITY_RATIO * d1:
            return _hold("ambiguous", f"two places named like {dest!r} at {d1 / 1000:.1f} and {d2 / 1000:.1f} km",
                         **common)
    best = max(near, key=lambda c: c.importance)
    if best is not near[0] and best.importance - near[0].importance >= PROMINENCE_MARGIN:
        return _hold("ambiguous", f"a better-known {best.name!r} is farther away than the nearest match", **common)
    place = near[0]
    if not _name_ok(place.name):
        return _hold("place_name", "the matched place name fails the name checks", **common)
    route = provider.route(origin, place)
    if not _route_ok(route, d1):
        return _hold("no_route", "no usable driving route", **common)
    return _answer(place.name, place, route, common, f"named place, {len(cands)} results, {len(near)} plausible")


def _nearest_of_type(spec, origin, provider, common):
    cands = provider.search(spec.query, origin, spec.radius_km, limit=20)
    keep = []
    for c in cands:
        if not set(c.kinds) & set(spec.kinds):
            continue
        if spec.cuisine and not set(c.cuisine) & set(spec.cuisine):
            continue
        if haversine_m(origin, (c.lat, c.lon)) > spec.radius_km * 1000.0:
            continue
        keep.append(c)
    # A nameless row is dropped as before. An address-shaped name stays in
    # the race: if it is the nearest, the answer HOLDS (naming the next
    # place "the nearest" would be wrong, and the address never renders).
    keep = [c for c in _dedupe(keep, origin) if _name_shape_ok(c.name)]
    if not keep:
        return _hold("not_found", f"no {spec.label} within {spec.radius_km:.0f} km", **common)
    best = None
    for c in keep[:TYPE_ROUTE_CANDIDATES]:
        d = haversine_m(origin, (c.lat, c.lon))
        if d < TOO_CLOSE_M:
            continue
        r = provider.route(origin, c)
        if _route_ok(r, d) and (best is None or r.distance_m < best[1].distance_m):
            best = (c, r)
    if best is None:
        return _hold("no_route", f"no usable route to the nearest {spec.label}", **common)
    place, route = best
    if not _name_ok(place.name):
        return _hold("place_name", f"the nearest {spec.label}'s name looks like a street address", **common)
    return _answer(f"The nearest {spec.label}, {place.name},", place, route, common,
                   f"nearest of type {spec.id} among {len(keep)}")
